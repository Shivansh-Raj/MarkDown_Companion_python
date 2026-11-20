# Markdown Companion V2 (Python / PySide6)

A tray-resident, always-on-top, retro-styled floating window for Markdown files.
Independent rewrite of V1 (Tauri/React) — **V1 is not touched**: V2 has its own code, dependencies,
data directory (`%APPDATA%\MarkdownCompanionV2`) and build output (`dist-v2\`, `build-v2\`).

## Run from source

```powershell
cd v2-python
py -3.11 -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\python run.py              # tray app; add file paths to open them immediately
```

Options: `--data-dir <dir>` (separate settings/logs), `--debug`. `MDC_NO_WEBENGINE=1` forces the
lightweight QTextBrowser fallback view.

## Usage

| Action | How |
|---|---|
| Home screen | **Ctrl+Shift+H**, the ⌂ button in any note window, tray icon click, or at startup: recent files, open windows, drag & drop `.md` files (can be turned off in Settings) |
| Open / focus main file | **Ctrl+Shift+M** (first press asks for a file, then remembers it) |
| Open any file | home screen button or drag & drop, tray → *Open Markdown file…*, or the optional "picker" shortcut |
| Several files | each file gets its own window; opening the same file again focuses it |
| Move / resize | drag the title bar / drag any edge or corner |
| Title-bar buttons | EDIT · ⌂ home · roll-up (compact) · refresh (F5) · cycle opacity · minimize · close (Ctrl+W) |
| Edit source | **EDIT** button (or Ctrl+E) → raw Markdown; **SAVE** / Ctrl+S writes the file, **CANCEL** / Esc leaves (press twice to discard unsaved text). If the file changed on disk meanwhile, the save is refused and your text is kept; pressing SAVE again deliberately overwrites. Line endings and BOM are preserved. |
| Tick a task | click its checkbox; the `.md` file is rewritten (only that `[ ]`↔`[x]`) |
| New file with AI | home screen → **New with AI**: describe the file, pick a type (or Auto-detect: plan / task list / scheduler / notes); a pixel cat types while the text streams in; **Save & open** writes it to the AI folder |
| Edit with AI | **AI** button in a note window: choose **your highlighted text**, a **heading section**, or the **whole file**, describe the change, review a red/green diff, **Apply**. The result lands in the edit draft (nothing is written until **SAVE**); **UNDO AI** steps back. Completed tasks stay completed, and text outside the chosen scope is never touched. |
| Settings, restart, exit | tray menu |

Closing a window never quits the app; use tray → *Exit*.
Window position, size, opacity and compact state are remembered per file. Windows from the last
session reopen at startup if enabled in Settings (off by default).

## Synchronization rules

The file on disk is the source of truth. Edits from VS Code/Obsidian/Notepad are picked up through
`watchdog` (debounced 150 ms; the parent directory is watched so atomic "save = rename" editors work;
a 5 s safety-net poll covers watcher failures). A change is only re-rendered if the SHA-256 of the
content differs, which also silences the echo of the app's own writes. The scroll position is kept
(the page body is swapped in place, not reloaded).

Checkbox clicks are applied to a **fresh read** of the file, only if the clicked line is still the same
task in the same state, and the file is re-verified right before an atomic replace (temp file + `os.replace`).
If an external edit got in the way, nothing is overwritten: the window reloads the latest content and
shows a banner. Files that are not valid UTF-8 are displayed but never modified.

## Tests

```powershell
.\.venv\Scripts\pip install -r requirements-dev.txt
.\.venv\Scripts\python -m pytest -q        # offscreen Qt, temp dirs only; never reads V1 or real user data
```

## Build a standalone executable (Windows)

```powershell
.\build.ps1       # installs deps, runs tests, runs PyInstaller -> dist-v2\MarkdownCompanionV2\MarkdownCompanionV2.exe
```

PyInstaller cannot cross-compile; build on Windows. The output is a folder (not one file) so that
startup is fast and anti-virus false positives are less likely. The build is **unsigned**; Windows
SmartScreen will warn on first run.

## Known limitations

- Global shortcuts use Win32 `RegisterHotKey` and are **only implemented for Windows**; on other
  platforms the app still runs and reports the failure. The Windows path has not yet been exercised on a
  real Windows machine (only unit-tested through an injected fake backend).
- Math (`$...$`, `$$...$$`) is shown as styled source text — no KaTeX is bundled, to stay offline and small.
- Raw HTML inside Markdown is escaped, deliberately.
- Rolled-up (compact) mode shows only the title bar.
- Only one running instance is allowed (lock file in the data directory).

## Layout

```
app/core      application, shortcut/window/file/settings managers
app/ui        markdown window, title bar, tray, settings dialog, file picker, views
app/markdown  renderer (markdown-it-py), checkbox logic, themes, HTML template
app/services  synchronization (Qt-free), file_watcher (watchdog), persistence (SQLite)
app/utils     paths, logging, geometry
assets/themes JSON palettes (retro, amber) + shared base.css; add a theme = add a JSON file
tests/        pytest suite
```


## AI (Groq)

Optional. Add a free API key from console.groq.com in **Settings → AI** (or the first time you use *New with AI*).
The key is stored in the OS keychain when the `keyring` package can reach one, otherwise in the local settings
database; it is sent only to `api.groq.com`. Use **Test** to check it. The model is chosen automatically from the
models your key can use. Settings also has the folder for AI-created files (default `~/Documents/MarkdownCompanion`)
and an **Animations** switch (typing cat, diff reveal). AI edits send the chosen text to Groq, so use the narrower
scopes for anything sensitive. Without a key the app behaves exactly as before.

## Notion

Connect Notion in Settings > Notion with a token from Notion's Developer portal. A *connection* token only reaches pages you
share with it (page menu > Add connections); a *personal access token* acts as you and reaches every page you can see.

The token is stored only in the operating system's credential store (Windows Credential Manager, macOS Keychain, or the Secret
Service on Linux) through the `keyring` package. **There is deliberately no plaintext fallback for it**, unlike the Groq key. If the
`keyring` package is missing from your environment you will see "The 'keyring' package is not installed ... nowhere secure to keep
the Notion token". Fix it with:

```
.\.venv\Scripts\pip install -r requirements.txt     # Windows
.venv/bin/pip install -r requirements.txt             # macOS / Linux
```

then restart the app. (`keyring` is listed in `requirements.txt`; it is only absent when the environment was created before
it was added.)

What you can do with Notion: send a note to a new page, open a Notion page as a linked note (home screen > Notion), refresh from or
push edits to it (with a preview and a conflict check), export meetings and practice sessions to Notion databases with their
columns mapped, turn action items into task rows, and see open tasks in the Notion tasks window (home screen > Notion tasks).

## Company AI (instead of Groq)

Settings > **AI provider** switches all chat features (Ask, AI edit, AI create, meeting analyses, coaching, follow-ups, Notion
Ask) between **Groq** and the **Company AI (gateway)**. Speech-to-text for recordings always uses Groq (the gateway only offers
chat), so keep a Groq key if you use Voice.

The company gateway works in two steps: V2 sends your client id and secret to `<auth URL>/generate-token`, then sends the chat
request to `<model URL>/chat/completions` with the token it got back. Replies are not streamed: the whole text appears at once.

Setup:
1. Put the credentials in a `.env` file next to the app (see `.env.example`): `CLIENT_ID=...` and `CLIENT_SECRET=...`
   (`client_id: "..."` style lines also work). `.env` is ignored by git. The credentials are read only from this file or from real
   environment variables, and are never saved in MC's settings.
2. Enter the two URLs and the model name under Settings > Company AI (or put `DAIA_AUTH_URL`, `DAIA_MODEL_URL`, `DAIA_MODEL` in
   `.env`). They are not part of the source code, so your company's host names stay out of the repository. The model defaults to
   `gpt-5.1`.
3. Press **Test connection**, then Apply.

**Certificates:** by default V2 verifies the server's certificate. If your network re-signs HTTPS traffic and the test fails with a
certificate message, untick "Verify the server's certificate" for this provider only (less safe).

