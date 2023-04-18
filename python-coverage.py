import sys
from collections import defaultdict
from pathlib import Path
from weakref import WeakSet

import sublime
import sublime_plugin

HERE = Path(__file__).parent

# References:
# https://coverage.readthedocs.io/en/stable/api_coveragedata.html#coverage.CoverageData
# https://www.sublimetext.com/docs/api_reference.html#sublime.View
# https://github.com/berendkleinhaneveld/sublime-doorstop/blob/main/doorstop_plugin.py
# https://python-watchdog.readthedocs.io/en/stable/

CACHE = {
    "open_folders": {},  # all folders that are being watched for .coverage files
    "coverage_files": {},  # all known coverage files
    # all known views (set of weakrefs) for which the file is in the coverage file (key)
    "registered_view_event_handlers": defaultdict(WeakSet),
    "coverage_for_file": {},  # map from file to corresponding coverage file
}

FILE_OBSERVER = None

FileWatcher = None

# TODO: show percentage in status?
# TODO: allow for project/window specific settings
SETTINGS_FILE = "python-coverage.sublime-settings"


def plugin_loaded():
    """
    Hook that is called by Sublime when plugin is loaded.
    """
    packaging_wheel = HERE / "libs" / "packaging-23.1-py3-none-any.whl"
    if str(packaging_wheel) not in sys.path:
        sys.path.append(str(packaging_wheel))

    from packaging.tags import sys_tags

    tags = [str(tag) for tag in sys_tags()]

    for prefix in {"coverage*", "watchdog*"}:
        # Figure out the right whl for the platform
        for wheel in (HERE / "libs").glob(prefix):
            wheel_tag = "-".join(wheel.stem.split("-")[2:])
            if wheel_tag in tags:
                break
        else:
            print(f"Could not find compatible {prefix} wheel for your platform")
            return

        if str(wheel) not in sys.path:
            sys.path.append(str(wheel))

    from watchdog.observers import Observer

    # TODO: stop watching/clear observer when disabling this plugin
    global FILE_OBSERVER
    FILE_OBSERVER = Observer()
    FILE_OBSERVER.start()

    from watchdog.events import FileSystemEventHandler

    class _FileWatcher(FileSystemEventHandler):
        def __init__(self):
            super().__init__()

        def _update(self, event):
            if not event.src_path.endswith(".coverage"):
                return

            if event.src_path not in CACHE["coverage_files"]:
                return

            CACHE["coverage_files"][event.src_path].update()
            if handlers := CACHE["registered_view_event_handlers"].get(event.src_path):
                for handler in handlers:
                    handler()._update_regions()

        def on_modified(self, event):
            self._update(event)

        def on_created(self, event):
            self._update(event)

    global FileWatcher
    FileWatcher = _FileWatcher()


def plugin_unloaded():
    """
    Hook that is called by Sublime when plugin is unloaded.
    """
    CACHE["coverage_files"].clear()
    CACHE["open_folders"].clear()
    CACHE["registered_view_event_handlers"].clear()
    CACHE["coverage_for_file"].clear()

    # TODO; next two lines should not be necessary
    # for watcher in CACHE["open_folders"].values():
    #     FILE_OBSERVER.remove_handler_for_watch(FileWatcher, watcher)

    global FILE_OBSERVER
    FILE_OBSERVER.stop()
    FILE_OBSERVER.join()
    FILE_OBSERVER = None


class CoverageFile:
    def __init__(self, coverage_file):
        import coverage

        self.coverage_file = coverage_file
        self.data = coverage.Coverage(data_file=coverage_file).get_data()
        self.data.read()

    def update(self):
        self.data.read()

    def contains_file(self, file):
        return str(file) in self.data.measured_files()

    def missing_lines(self, file, text):
        from coverage.exceptions import DataError
        from coverage.parser import PythonParser

        try:
            lines = self.data.lines(file)
        except DataError:
            return None
        if lines is None:
            return None

        # TODO: Maybe this could be cached? And use file watcher to invalidate?
        # TODO: maybe a rust version of this could save some time?
        import time

        start = time.perf_counter()
        python_parser = PythonParser(text=text)
        python_parser.parse_source()
        statements = python_parser.statements
        end = time.perf_counter()

        print(f"parsing {file} took: {end - start}")

        return sorted(list(statements - set(lines)), reverse=True)


class ToggleMissingLinesCommand(sublime_plugin.ApplicationCommand):
    def run(self):
        settings = sublime.load_settings(SETTINGS_FILE)
        settings["show_missing_lines"] = not settings["show_missing_lines"]
        sublime.save_settings(SETTINGS_FILE)
        print(
            "Python Coverage: "
            f"{'Enabled' if settings['show_missing_lines'] else 'Disabled'}"
            " show missing lines"
        )


class PythonCoverageDataFileListener(sublime_plugin.EventListener):
    @classmethod
    def is_applicable(cls, settings):
        """
        Returns:
            Whether this listener should apply to a view with the given Settings.
        """
        return True

    def on_new_project_async(self, window):
        """
        Called right after a new project is created, passed the Window object.
        Runs in a separate thread, and does not block the application.
        """
        self.update_available_coverage_files(window)

    def on_load_project_async(self, window):
        """
        Called right after a project is loaded, passed the Window object.
        Runs in a separate thread, and does not block the application.
        """
        self.update_available_coverage_files(window)

    def on_post_save_project_async(self, window):
        """
        Called right after a project is saved, passed the Window object.
        Runs in a separate thread, and does not block the application.
        """
        self.update_available_coverage_files(window)

    def on_pre_close_project(self, window):
        """
        Called right before a project is closed, passed the Window object.
        """
        self.update_available_coverage_files(window)

    def on_activated_async(self, view):
        if window := view.window():
            self.update_available_coverage_files(window)

    def update_available_coverage_files(self, window):
        settings = sublime.load_settings(SETTINGS_FILE)
        if not settings["show_missing_lines"]:
            return
        for folder in window.folders():
            # In order to save some IO:
            # - find the currently files here once for the given folder
            #   and mark the folder (so it won't be scanned again)
            # - then add a watcher to that folder that watches recursively
            #   for any .coverage files

            # Check if folder is already watched (either direct or as a subdirectory)
            if any(fold in folder for fold in CACHE["open_folders"]):
                return

            CACHE["open_folders"][folder] = FILE_OBSERVER.schedule(
                FileWatcher, folder, recursive=True
            )
            print(f"Watch folder: {folder}")

            for coverage_file in Path(folder).glob("**/.coverage"):
                if (
                    coverage_file.is_file()
                    and coverage_file not in CACHE["coverage_files"]
                ):
                    CACHE["coverage_files"][str(coverage_file)] = CoverageFile(
                        coverage_file
                    )


class PythonCoverageEventListener(sublime_plugin.ViewEventListener):
    @classmethod
    def is_applicable(cls, settings):
        """
        Returns:
            Whether this listener should apply to a view with the given Settings.
        """
        return "Python" in settings.get("syntax", "")

    def on_modified_async(self):
        """
        Called after changes have been made to the view.
        Runs in a separate thread, and does not block the application.
        """
        pass
        # TODO: clear the modified region(s), if any

    def on_activated_async(self):
        """
        Called when a view gains input focus. Runs in a separate thread,
        and does not block the application.
        """
        # FIXME: this is not always called for 'cloned'? views?
        # Maybe change to EventListener instead and just iterate through all the
        # (Python) views?
        # Also keep a dict of filenames with cached parsed results
        # The file watcher can remove the cached entries on any change

        settings = sublime.load_settings(SETTINGS_FILE)
        if not settings["show_missing_lines"]:
            self.view.erase_regions(key="python-coverage")
            return

        self._update_regions()

    def _update_regions(self):
        file_name = self.view.file_name()
        if not file_name:
            print("View without file_name")
            return

        coverage_file = CACHE["coverage_for_file"].get(file_name)

        if not coverage_file:
            for coverage_file in CACHE["coverage_files"].values():
                if coverage_file.contains_file(file_name):
                    CACHE["coverage_for_file"][file_name] = coverage_file
                    break
            else:
                print(f"File not in any coverage file: {file_name}")
                self.view.erase_regions(key="python-coverage")
                return

        CACHE["registered_view_event_handlers"][file_name].add(self.view)
        print(
            f"#views for {file_name}: "
            f'{len(CACHE["registered_view_event_handlers"][file_name])}'
        )

        full_file_region = sublime.Region(0, self.view.size())
        text = self.view.substr(full_file_region)

        missing = coverage_file.missing_lines(file_name, text)
        if not missing:
            self.view.erase_regions(key="python-coverage")
            return

        all_lines_regions = self.view.lines(full_file_region)
        missing_regions = [all_lines_regions[line - 1] for line in missing]

        self.view.add_regions(
            key="python-coverage",
            regions=missing_regions,
            scope="region.orangish",
            icon="Packages/sublime-python-coverage/images/triangle.png",
            flags=sublime.RegionFlags.HIDDEN,
        )

    def on_hover(self, point, hover_zone):
        """
        Called when the user's mouse hovers over a view for a short period.
        """
        if hover_zone != sublime.HoverZone.GUTTER:
            return

        regions = self.view.get_regions("python-coverage")
        if not regions:
            return

        for region in regions:
            if region.contains(point):
                break
        else:
            return

        self.view.show_popup(
            "Coverage: uncovered line",
            sublime.HIDE_ON_MOUSE_MOVE_AWAY,
            point,
            500,
            500,
            None,
        )
