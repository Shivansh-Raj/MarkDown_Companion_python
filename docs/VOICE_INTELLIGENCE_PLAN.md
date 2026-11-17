# Voice Intelligence + Ask AI — port plan for V2 (Python / PySide6)

V1 (Tauri) already has all of this (see its `docs/VOICE_INTELLIGENCE_PLAN.md`). This plan ports the same product to V2 and
records where V2 differs. Same behaviour, same safety rules, same file/session layout.

## 1. What V2 already gives us

| Area | V2 today | Reuse |
|---|---|---|
| AI | `app/ai/groq.py` (stdlib `urllib`, model discovery, SSE streaming), `worker.py` (QThread `StreamWorker`), `services.py` (key in OS keychain, save dir) | chat analysis, Ask AI; add a Whisper client next to it |
| Windows | frameless pixel-style windows (`MarkdownWindow`, `HomeWindow`, `AiCreateWindow`), `TitleBar`, FlowLayout, QSS 3D look | `VoiceWindow` uses the same chrome |
| Files | `FileManager`/`WindowManager` open, watch, save (hash-checked); `create.py` writes new files without overwriting | export, append, open in floating windows |
| Storage | SQLite settings (`SettingsManager`), `app_data_dir()` | session folders under `app_data_dir()/voice/` |
| Safety | AI edit panel splices only the chosen scope, `preserve_progress`, stale-file refusal | append-to-file export reuses the same hash-checked save |

## 2. Decisions (and how V2 differs from V1)

1. **Audio capture: `QtMultimedia.QAudioSource`** (ships with PySide6, no new dependency). Qt converts the microphone to
   16 kHz mono 16-bit PCM for us (verified supported on the default device); if a device refuses that format a pure-Python
   converter handles the device's own format. V1 used `cpal`.
2. **Speech-to-text: Groq Whisper (`whisper-large-v3-turbo`)**, stdlib `urllib` multipart upload in a worker thread. Same
   rolling chunks cut at silence, same word timestamps, same explicit cloud-consent step. Behind a `Transcriber` interface so a
   local engine can replace it later.
3. **Pure logic first, in `app/voice/`, fully pytest-covered**: `wav`, `chunker`, `session`, `transcript`, `analytics`,
   `analyses`, `coach`, `followups`, `ask`. These are line-for-line ports of V1's tested TypeScript, so behaviour matches.
4. **Recording indicator:** macOS/Windows tray icons cannot show text, so the tray icon switches to a red-dot variant and its
   tooltip shows `● REC 03:12` / `❚❚ PAUSED 03:12`; the voice window shows a REC pill. (V1 can put text in the macOS menu bar.)
5. **Threading:** capture runs on Qt's audio thread; chunk writing/transcription/AI run in `QThread` workers; the UI never blocks.
6. **Persistence:** `voice/<id>/{session.json, audio.wav, chunks/NNNN.wav, transcript.json, analyses/*.json}`, atomic writes,
   crash recovery repairs the WAV header and marks sessions `interrupted`. Identical to V1.
7. **Not promised (same as V1):** system-audio capture (use a loopback device as the microphone), word-by-word streaming
   transcription, pitch/volume analysis, local transcription.

## 3. Module layout

```
app/voice/            (Qt-free, pytest-covered)
  wav.py chunker.py session.py transcript.py analytics.py analyses.py coach.py followups.py ask.py whisper.py
  capture.py          Qt: QAudioSource wrapper + worker that writes WAV/chunks and emits signals
app/ui/voice_window.py            workspace: cards, recorder, review, recent sessions
app/ui/voice_panels.py            meeting analyses, practice coach, follow-ups, export dialog
app/ui/ask_panel.py               "Ask AI about this file" panel inside MarkdownWindow
```

## 4. Phases (each independently testable; same order as V1)

| # | Scope | Status |
|---|---|---|
| 1 | Audio core: WAV writer/repair, chunker, session store + recovery, QtMultimedia recorder (start/pause/resume/stop, device list, level, tray REC) | **in progress** |
| 2 | Whisper transcription queue, Voice window, consent, session list, Quick voice note → Markdown | **done** |
| 3 | Meeting analyses + templates + export (new/append/separate, hash-checked) | **done** |
| 4 | Practice mode: local speaking analytics + AI coach (quote-validated) | **done** |
| 5 | Follow-up detector, live question suggestions (opt-in), RETHINK, session search/filter/delete, re-transcribe | **done** |
| 6 | Ask AI about the open file (grounded answers, verified quotes) | **done** |

## 5. Risks and mitigations

| Risk | Mitigation |
|---|---|
| macOS microphone permission (script run from a terminal inherits the terminal's permission) | clear "no sound" warning after 3 s of silence; document how to grant access; packaged app needs `NSMicrophoneUsageDescription` |
| Qt audio quirks per platform | format negotiation + fallback converter; device list refreshed on start |
| Whisper hallucinating on silence | RMS gate + phantom-phrase filter (ported) |
| Rate limits / failures | per-chunk retry with backoff, RETRY button, audio kept, re-transcribe later |
| Crash mid-recording | incremental WAV + recovery on next launch |
| Privacy | one-time cloud consent, visible REC state, per-session delete audio/session, suggestions opt-in |

## 6. Testing

pytest for every pure module (ported from V1's vitest cases) and for the Qt pieces with a fake audio device
(`MDC_FAKE_MIC` feeds synthetic PCM so recorder/transcription/UI flows run headless). Real-microphone behaviour (permission
prompt, pause/resume timing, unplugging, long recordings) can only be checked by hand.

## 7. Dependencies / packaging

No new Python packages (QtMultimedia is part of PySide6; PyInstaller spec needs the multimedia plugins and a
`NSMicrophoneUsageDescription` in the bundle's Info.plist on macOS).
