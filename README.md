# Jot_

A frameless, translucent side-panel note-taking app for Windows. Docks to the
right edge of the screen for instant note capture, with optional AI-assisted
writing (Polish / Draft Email / Expand Idea) via the Anthropic API.

## Requirements

- Windows
- Python 3.10+
- Dependencies in `requirements.txt`

## Setup

```
pip install -r requirements.txt
python jot8_14_3.py
```

On first launch, click the 🔑 button to add your own Anthropic API key if you
want to use the AI features. The key is stored locally in `settings.json`
(never committed to this repo) and is not required for basic note-taking.

## Notes

- `fonts/ModularBlackBlockyBoldModern.ttf` is used for button styling if
  present; the app falls back to Open Sans if it's missing.
- The global hotkey (`Alt+N`) is registered via the `keyboard` package, which
  may require running as administrator on some systems.
- `jots.json` and `settings.json` are created at runtime next to the script
  (or the compiled `.exe`) and are excluded from version control.

## Building a standalone .exe

```
pyinstaller --onefile --windowed jot8_14_3.py
```
