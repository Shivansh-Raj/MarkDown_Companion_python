# Setup & Run on Windows

Markdown Companion V2 is a Python/PySide6 tray app. Follow these steps on Windows 10/11 (64-bit).

## 1. Prerequisites
- **Python 3.11 or newer (64-bit)** from <https://www.python.org/downloads/>. In the installer tick **"Add python.exe to PATH"**.
- **Git** (only to clone): <https://git-scm.com/download/win>
- Internet access for the first install (about 500 MB of dependencies). Afterwards the app is fully offline.

Verify in PowerShell:
```powershell
py -3.11 --version
git --version
```

## 2. Get the code
```powershell
git clone https://github.com/Shivansh-Raj/MarkDown_Companion.git
cd MarkDown_Companion
```

## 3. Install
```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python -m pip install --upgrade pip
.\.venv\Scripts\pip install -r requirements.txt
```
If the later steps say "running scripts is disabled", run `Set-ExecutionPolicy -Scope Process Bypass` in that window first.

## 4. Run
```powershell
.\.venv\Scripts\python run.py
```
A **home screen** opens (recent files, open windows, "Open file…", drag & drop). The app also lives in the **system tray** (look under the `^` overflow arrow); clicking the tray icon brings the home screen back.

- Press **Ctrl+Shift+M** from any app: the first time you choose a `.md` file, afterwards it re-focuses that file's window.
- Left-click the tray icon, or right-click it for *Open Markdown file, Open recent, Manage open windows, Settings, Restart, Exit*.
- You can also open files directly: `.\.venv\Scripts\python run.py C:\notes\todo.md C:\notes\ideas.md`
- Closing a window does **not** quit the app; use tray → **Exit**.

Settings, window positions and logs are stored in `%APPDATA%\MarkdownCompanionV2\` (log: `companion.log`).

## 5. Run the tests (optional)
```powershell
.\.venv\Scripts\pip install -r requirements-dev.txt
.\.venv\Scripts\python -m pytest -q
```

## 6. Build a standalone .exe (optional)
```powershell
.\build.ps1
```
Output: `dist-v2\MarkdownCompanionV2\MarkdownCompanionV2.exe`. Copy the **whole folder** to another PC; Python is not needed there.
The exe is unsigned, so SmartScreen shows a warning: **More info → Run anyway**. On a very clean Windows install you may need the
[Microsoft Visual C++ Redistributable](https://learn.microsoft.com/cpp/windows/latest-supported-vc-redist).

## Troubleshooting
| Problem | Fix |
|---|---|
| "Shortcut problem" notification | Another app owns Ctrl+Shift+M. Tray → Settings → choose a different shortcut → Apply. |
| No tray icon | Click the `^` arrow in the taskbar; or Settings → Personalization → Taskbar → "Other system tray icons". |
| "already running" message | Only one instance is allowed; use the existing tray icon. |
| Blank window / rendering problems | Start with `set MDC_NO_WEBENGINE=1` then `.\.venv\Scripts\python run.py` (simple fallback view) and report the log. |
| Anything else | Run with `--debug` and check `%APPDATA%\MarkdownCompanionV2\companion.log`. |

> Status: developed and tested on macOS (48 automated tests). The Windows hotkey and the .exe build have not yet been
> verified on a real Windows machine. Please report anything that misbehaves.
