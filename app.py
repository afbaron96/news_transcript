import glob
import html
import os
import re
import tempfile
import threading
import time
import webbrowser
from typing import Optional

from flask import Flask, request, jsonify, render_template_string
import yt_dlp

app = Flask(__name__)


HTML_PAGE = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Jess' Transcript Extractor</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <style>
    * { box-sizing: border-box; }

    body {
      margin: 0;
      font-family: Helvetica, Arial, sans-serif;
      background: #f0f2f5;
      color: #1c1e21;
    }

    .page {
      max-width: 1320px;
      margin: 24px auto;
      padding: 0 16px;
    }

    .card {
      background: #ffffff;
      border-radius: 18px;
      padding: 28px 32px;
      box-shadow: 0 1px 2px rgba(0,0,0,0.08), 0 8px 24px rgba(0,0,0,0.06);
      border: 1px solid #e4e6eb;
    }

    h1 {
      margin: 0 0 6px 0;
      font-size: 34px;
      font-weight: 700;
      color: #1f2937;
      letter-spacing: -0.5px;
    }

    .controls {
      display: flex;
      gap: 16px;
      align-items: center;
      margin-bottom: 18px;
    }

    input[type="text"] {
      flex: 1;
      padding: 15px 18px;
      font-size: 16px;
      border: 1px solid #ccd0d5;
      border-radius: 14px;
      background: #ffffff;
      outline: none;
      transition: border-color 0.2s ease, box-shadow 0.2s ease;
    }

    input[type="text"]:focus {
      border-color: #1877f2;
      box-shadow: 0 0 0 3px rgba(24, 119, 242, 0.15);
    }

    button {
      border: none;
      border-radius: 14px;
      padding: 15px 22px;
      font-size: 16px;
      font-weight: 600;
      cursor: pointer;
      transition: transform 0.05s ease, opacity 0.2s ease, background 0.2s ease;
    }

    button:active {
      transform: scale(0.98);
    }

    button.primary {
      background: #1877f2;
      color: white;
      min-width: 170px;
    }

    button.primary:hover {
      background: #166fe5;
    }

    button.primary:disabled {
      opacity: 0.7;
      cursor: not-allowed;
    }

    button.secondary {
      background: #e4e6eb;
      color: #1c1e21;
      min-width: 92px;
    }

    button.secondary:hover {
      background: #d8dadf;
    }

    .status {
      min-height: 26px;
      margin: 8px 0 26px 0;
      font-size: 15px;
      color: #4b5563;
      white-space: pre-wrap;
    }

    .meta {
      display: grid;
      grid-template-columns: 140px 1fr;
      gap: 10px 18px;
      margin-bottom: 22px;
      font-size: 17px;
    }

    .meta .label {
      font-weight: 700;
      color: #374151;
    }

    textarea {
      width: 100%;
      min-height: 580px;
      resize: vertical;
      border: 1px solid #ccd0d5;
      border-radius: 14px;
      padding: 20px;
      font-size: 18px;
      line-height: 1.45;
      font-family: "SFMono-Regular", Menlo, Consolas, monospace;
      background: #f7f8fa;
      color: #111827;
      outline: none;
    }

    textarea:focus {
      border-color: #1877f2;
      box-shadow: 0 0 0 3px rgba(24, 119, 242, 0.12);
    }

    .hidden {
      display: none;
    }

    @media (max-width: 900px) {
      .controls {
        flex-direction: column;
        align-items: stretch;
      }

      button.primary,
      button.secondary {
        width: 100%;
      }

      .meta {
        grid-template-columns: 1fr;
        gap: 4px 0;
      }

      .meta .label {
        margin-top: 10px;
      }
    }
  </style>
</head>
<body>
  <div class="page">
    <div class="card">
      <h1>Jess' Transcript Extractor</h1>

      <div class="controls">
        <input id="urlInput" type="text" placeholder="Paste YouTube URL here..." />
        <button id="getBtn" class="primary" onclick="getTranscript()">Get Transcript</button>
        <button class="secondary" onclick="clearAll()">Clear</button>
      </div>

      <div id="status" class="status"></div>

      <div id="meta" class="meta hidden">
        <div class="label">Title</div><div id="metaTitle"></div>
        <div class="label">Language</div><div id="metaLanguage"></div>
        <div class="label">Source</div><div id="metaSource"></div>
        <div class="label">Video ID</div><div id="metaVideoId"></div>
      </div>

      <textarea id="transcriptBox" placeholder="Transcript will appear here..." readonly></textarea>
    </div>
  </div>

  <script>
    function setLoading(isLoading) {
      const btn = document.getElementById("getBtn");
      const status = document.getElementById("status");

      if (isLoading) {
        btn.disabled = true;
        btn.textContent = "Loading...";
        status.textContent = "Fetching transcript...";
      } else {
        btn.disabled = false;
        btn.textContent = "Get Transcript";
      }
    }

    async function getTranscript() {
      const url = document.getElementById("urlInput").value.trim();
      const status = document.getElementById("status");
      const transcriptBox = document.getElementById("transcriptBox");
      const meta = document.getElementById("meta");

      if (!url) {
        status.textContent = "Please enter a YouTube URL.";
        return;
      }

      setLoading(true);

      try {
        const response = await fetch("/api/transcript", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ url })
        });

        const data = await response.json();

        if (!response.ok) {
          throw new Error(data.error || "Unknown error");
        }

        document.getElementById("metaTitle").textContent = data.title;
        document.getElementById("metaLanguage").textContent = data.language;
        document.getElementById("metaSource").textContent = data.source;
        document.getElementById("metaVideoId").textContent = data.video_id;
        meta.classList.remove("hidden");

        transcriptBox.value = data.transcript;
        status.textContent = "Transcript loaded successfully.";
      } catch (err) {
        status.textContent = "Error: " + err.message;
      } finally {
        setLoading(false);
      }
    }

    function clearAll() {
      document.getElementById("urlInput").value = "";
      document.getElementById("status").textContent = "";
      document.getElementById("transcriptBox").value = "";
      document.getElementById("meta").classList.add("hidden");
      document.getElementById("metaTitle").textContent = "";
      document.getElementById("metaLanguage").textContent = "";
      document.getElementById("metaSource").textContent = "";
      document.getElementById("metaVideoId").textContent = "";
      document.getElementById("urlInput").focus();
    }

    document.getElementById("urlInput").addEventListener("keydown", function(e) {
      if (e.key === "Enter") {
        getTranscript();
      }
    });

    window.onload = function() {
      document.getElementById("urlInput").focus();
    };
  </script>
</body>
</html>
"""


def extract_video_id(url: str) -> Optional[str]:
    patterns = [
        r"(?:v=)([a-zA-Z0-9_-]{11})",
        r"(?:youtu\.be/)([a-zA-Z0-9_-]{11})",
        r"(?:embed/)([a-zA-Z0-9_-]{11})",
        r"(?:shorts/)([a-zA-Z0-9_-]{11})",
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None


def parse_vtt(vtt_content: str) -> str:
    lines = vtt_content.splitlines()
    seen = []
    result = []

    for line in lines:
        line = line.strip()

        if (
            not line
            or line.startswith("WEBVTT")
            or line.startswith("Kind:")
            or line.startswith("Language:")
            or "-->" in line
            or re.match(r"^\d+$", line)
        ):
            continue

        line = html.unescape(line)
        line = re.sub(r"<[^>]+>", "", line).strip()

        if line and line not in seen:
            seen.append(line)
            result.append(line)
            if len(seen) > 5:
                seen.pop(0)

    return "\n".join(result)


def choose_best_lang(available_langs: list[str], preferred_langs: list[str]) -> Optional[str]:
    if not available_langs:
        return None

    for lang in preferred_langs:
        if lang in available_langs:
            return lang

    for lang in preferred_langs:
        prefix = lang.split("-")[0]
        for available in available_langs:
            if available == prefix or available.startswith(prefix + "-"):
                return available

    return available_langs[0]


def list_downloaded_subtitle_files(tmpdir: str) -> list[str]:
    patterns = [
        os.path.join(tmpdir, "*.vtt"),
        os.path.join(tmpdir, "*.ttml"),
        os.path.join(tmpdir, "*.srv3"),
        os.path.join(tmpdir, "*.srv2"),
        os.path.join(tmpdir, "*.srv1"),
        os.path.join(tmpdir, "*.json3"),
    ]

    files = []
    for pattern in patterns:
        files.extend(glob.glob(pattern))
    return files


def read_subtitle_file(path: str) -> str:
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        raw = f.read()

    ext = os.path.splitext(path)[1].lower()
    if ext == ".vtt":
        return parse_vtt(raw)

    return html.unescape(raw).strip()


def download_subtitles_with_ytdlp(
    url: str,
    tmpdir: str,
    preferred_langs: list[str],
    use_cookies: bool,
) -> tuple[dict, str, str]:
    info_opts = {
        "skip_download": True,
        "quiet": True,
        "no_warnings": True,
        "ignoreconfig": True,
        "noplaylist": True,
        "extract_flat": False,
    }

    if use_cookies:
        info_opts["cookiesfrombrowser"] = ("chrome",)

    with yt_dlp.YoutubeDL(info_opts) as ydl:
        info = ydl.extract_info(url, download=False)

    subtitles = info.get("subtitles") or {}
    automatic_captions = info.get("automatic_captions") or {}

    selected_lang = choose_best_lang(list(subtitles.keys()), preferred_langs)
    source_type = "manual subtitles"

    if selected_lang is None:
        selected_lang = choose_best_lang(list(automatic_captions.keys()), preferred_langs)
        source_type = "automatic captions"

    if selected_lang is None:
        raise RuntimeError("No subtitles or automatic captions available for this video.")

    download_opts = {
        "skip_download": True,
        "quiet": True,
        "no_warnings": True,
        "ignoreconfig": True,
        "noplaylist": True,
        "writesubtitles": source_type == "manual subtitles",
        "writeautomaticsub": source_type == "automatic captions",
        "subtitleslangs": [selected_lang],
        "subtitlesformat": "vtt/best",
        "outtmpl": os.path.join(tmpdir, "transcript.%(ext)s"),
    }

    if use_cookies:
        download_opts["cookiesfrombrowser"] = ("chrome",)

    with yt_dlp.YoutubeDL(download_opts) as ydl:
        ydl.extract_info(url, download=True)

    files = list_downloaded_subtitle_files(tmpdir)
    if not files:
        raise RuntimeError("Subtitle download completed, but no subtitle file was found.")

    return info, selected_lang, source_type


def fetch_transcript(url: str, video_id: str):
    preferred_langs = ["en", "en-US", "en-GB"]
    last_error = None

    with tempfile.TemporaryDirectory() as tmpdir:
        info = None
        lang_used = None
        source_type = None

        for use_cookies in [True, False]:
            try:
                info, lang_used, source_type = download_subtitles_with_ytdlp(
                    url=url,
                    tmpdir=tmpdir,
                    preferred_langs=preferred_langs,
                    use_cookies=use_cookies,
                )
                break
            except Exception as e:
                last_error = e

        if info is None or lang_used is None or source_type is None:
            raise RuntimeError(f"Could not download subtitles. Last error: {last_error}")

        title = info.get("title", video_id)

        files = list_downloaded_subtitle_files(tmpdir)
        if not files:
            raise RuntimeError("No downloaded subtitle file found after yt-dlp finished.")

        subtitle_path = files[0]
        text = read_subtitle_file(subtitle_path)

        if not text.strip():
            raise RuntimeError("Subtitle file was downloaded but parsed as empty.")

        return text, lang_used, title, source_type


@app.route("/", methods=["GET"])
def index():
    return render_template_string(HTML_PAGE)


@app.route("/api/transcript", methods=["POST"])
def api_transcript():
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()

    if not url:
        return jsonify({"error": "No URL provided."}), 400

    video_id = extract_video_id(url)
    if not video_id:
        return jsonify({"error": "Could not extract a valid YouTube video ID."}), 400

    try:
        transcript, lang, title, source_type = fetch_transcript(url, video_id)
        return jsonify(
            {
                "title": title,
                "language": lang,
                "source": source_type,
                "video_id": video_id,
                "transcript": transcript,
            }
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def open_browser():
    time.sleep(1.0)
    webbrowser.open("http://127.0.0.1:5000")


if __name__ == "__main__":
    threading.Thread(target=open_browser, daemon=True).start()
    app.run(host="127.0.0.1", port=5000, debug=False, use_reloader=False)