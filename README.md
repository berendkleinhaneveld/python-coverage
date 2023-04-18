# Sublime Python Coverage

Sublime Text plugin to show missing lines of python code coverage

<img width="260" alt="Missing lines highlighted in Sublime" src="https://user-images.githubusercontent.com/1000968/232587869-38e6a6a7-ad56-44a3-bc92-b4e90057c911.png">

## Usage

First enable by running the 'Python Coverage: Toggle Missing Lines' command.
When you open a folder that contains a `.coverage` file, it will highlight missing lines for files that are represented in the coverage file.

Tip: use pytest-watch together with pytest-cov to automatically trigger your pytest suite and update the coverage while you work in Sublime:

```sh
ptw --clear --nobeep -- --cov=.
```
