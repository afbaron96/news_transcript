# Jess' Transcript Extractor

Small Flask app that extracts YouTube subtitles or automatic captions and shows them in a local browser page.

## Run locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

On Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
```

## What it does

- Opens a local page in your browser
- Lets you paste a YouTube URL
- Fetches manual subtitles when available
- Falls back to automatic captions when needed
- Decodes escaped characters like `&gt;`, `&lt;`, and `&amp;`

## Build from GitHub Actions

This repo includes a workflow that builds:

- a Windows executable with PyInstaller
- a macOS binary with PyInstaller

Open the **Actions** tab in GitHub and run **Build desktop binaries** manually.
Then download the artifacts from the workflow run.

## Notes

- The Windows `.exe` is built on GitHub's Windows runner, not on macOS locally.
- The app opens your default browser at `http://127.0.0.1:5000`.
- Closing the browser tab does not always stop the process; stop the app in the terminal if needed.

Windows: winget install ffmpeg