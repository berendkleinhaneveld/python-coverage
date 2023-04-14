import sys
from pathlib import Path

import sublime
import sublime_plugin

HERE = Path(__file__).parent

# References:
# https://coverage.readthedocs.io/en/stable/api_coveragedata.html#coverage.CoverageData
# https://www.sublimetext.com/docs/api_reference.html#sublime.View
# https://github.com/berendkleinhaneveld/sublime-doorstop/blob/main/doorstop_plugin.py


def plugin_loaded():
    """
    Hook that is called by Sublime when plugin is loaded.
    """
    packaging_wheel = HERE / "libs" / "packaging-23.1-py3-none-any.whl"
    if str(packaging_wheel) not in sys.path:
        sys.path.append(str(packaging_wheel))

    from packaging.tags import sys_tags

    tags = [str(tag) for tag in sys_tags()]

    # Figure out the right whl for the platform
    for wheel in (HERE / "libs").glob("coverage*"):
        wheel_tag = "-".join(wheel.stem.split("-")[2:])
        if wheel_tag in tags:
            break
    else:
        print("Could not find compatible coverage wheel for your platform")
        return

    if str(wheel) not in sys.path:
        sys.path.append(str(wheel))


def plugin_unloaded():
    """
    Hook that is called by Sublime when plugin is unloaded.
    """
    pass


class PythonCoverageEventListener(sublime_plugin.ViewEventListener):
    @classmethod
    def is_applicable(cls, settings):
        """
        Returns:
            Whether this listener should apply to a view with the given Settings.
        """
        return "Python" in settings.get("syntax", "")

    def on_activated_async(self):
        """
        Called when a view gains input focus. Runs in a separate thread,
        and does not block the application.
        """
        # TODO: use setting/command to toggle this functionality
        file_name = self.view.file_name()
        if not file_name:
            return

        window = sublime.active_window()
        for folder in window.folders():
            folder = Path(folder)
            coverage_file = folder / ".coverage"
            if not coverage_file.is_file():
                continue

            import coverage
            from coverage import parser

            # TODO: add file watcher to coverage file and keep
            # the CoverageData object cached somewhere
            full_file_region = sublime.Region(0, self.view.size())
            data = coverage.Coverage(data_file=coverage_file).get_data()
            data.read()
            lines = data.lines(file_name)
            if lines is None:
                self.view.erase_regions(key="python-coverage")
                return

            text = self.view.substr(full_file_region)
            python_parser = parser.PythonParser(text=text)
            python_parser.parse_source()
            statements = python_parser.statements

            missing = sorted(list(statements - set(lines)), reverse=True)

            all_lines_regions = self.view.lines(full_file_region)
            missing_regions = [all_lines_regions[line - 1] for line in missing]

            self.view.add_regions(
                key="python-coverage",
                regions=missing_regions,
                scope="region.orangish",
                # TODO: create/use better icon for gutter
                icon="dot",
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
