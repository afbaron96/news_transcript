import glob
import html
import os
import re
import tempfile
import threading
import time
import uuid
import webbrowser
from typing import Optional

from flask import Flask, request, jsonify, render_template_string
import yt_dlp

app = Flask(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

# Approximate CPU speed factor per model: seconds of processing per second of audio
WHISPER_MODELS = {
    "tiny":   {"factor": 0.15, "label": "Tiny  (fastest, lower quality)"},
    "base":   {"factor": 0.35, "label": "Base  (fast, good quality) ✦ default"},
    "small":  {"factor": 0.80, "label": "Small (moderate, great quality)"},
    "medium": {"factor": 2.00, "label": "Medium (slow, excellent quality)"},
    "large":  {"factor": 5.00, "label": "Large  (slowest, best quality)"},
}
DEFAULT_MODEL = "base"

# ---------------------------------------------------------------------------
# Whisper model cache (loaded lazily, one instance per model size)
# ---------------------------------------------------------------------------
_whisper_models: dict = {}
_whisper_lock = threading.Lock()


def get_whisper_model(size: str):
    if size in _whisper_models:
        return _whisper_models[size]
    with _whisper_lock:
        if size not in _whisper_models:
            try:
                import whisper
                _whisper_models[size] = whisper.load_model(size)
            except ImportError as exc:
                raise RuntimeError(
                    "openai-whisper no esta instalado. "
                    "Ejecuta: pip install openai-whisper"
                ) from exc
    return _whisper_models[size]


# ---------------------------------------------------------------------------
# Job store  { job_id -> JobState }
# ---------------------------------------------------------------------------

class JobState:
    def __init__(self):
        self.phase: str = "starting"      # starting | subtitles | audio | whisper | done | error
        self.phase_label: str = "Starting..."
        self.progress: float = 0.0        # 0-100
        self.audio_duration: float = 0.0  # seconds, filled once known
        self.whisper_start: float = 0.0   # time.monotonic() when whisper began
        self.model_size: str = DEFAULT_MODEL
        # result fields
        self.done: bool = False
        self.error: Optional[str] = None
        self.transcript: Optional[str] = None
        self.title: Optional[str] = None
        self.language: Optional[str] = None
        self.source: Optional[str] = None
        self.video_id: Optional[str] = None

_jobs: dict = {}
_jobs_lock = threading.Lock()

def new_job():
    job_id = str(uuid.uuid4())
    state = JobState()
    with _jobs_lock:
        _jobs[job_id] = state
    return job_id, state

def get_job(job_id: str) -> Optional[JobState]:
    with _jobs_lock:
        return _jobs.get(job_id)

def cleanup_old_jobs():
    """Keep the job store from growing forever (keep last 50)."""
    with _jobs_lock:
        if len(_jobs) > 50:
            oldest = list(_jobs.keys())[:-50]
            for k in oldest:
                del _jobs[k]


# ---------------------------------------------------------------------------
# HTML page
# ---------------------------------------------------------------------------

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
      background: #fff;
      border-radius: 18px;
      padding: 28px 32px;
      box-shadow: 0 1px 2px rgba(0,0,0,.08), 0 8px 24px rgba(0,0,0,.06);
      border: 1px solid #e4e6eb;
    }

    h1 {
      margin: 0 0 20px;
      font-size: 34px;
      font-weight: 700;
      color: #1f2937;
      letter-spacing: -.5px;
    }

    .controls {
      display: flex;
      gap: 12px;
      align-items: center;
      margin-bottom: 14px;
    }

    input[type="text"] {
      flex: 1;
      padding: 14px 18px;
      font-size: 16px;
      border: 1px solid #ccd0d5;
      border-radius: 14px;
      outline: none;
      transition: border-color .2s, box-shadow .2s;
    }
    input[type="text"]:focus {
      border-color: #1877f2;
      box-shadow: 0 0 0 3px rgba(24,119,242,.15);
    }

    select {
      padding: 14px 12px;
      font-size: 15px;
      border: 1px solid #ccd0d5;
      border-radius: 14px;
      background: #fff;
      color: #1c1e21;
      outline: none;
      cursor: pointer;
      transition: border-color .2s;
    }
    select:focus { border-color: #1877f2; }

    button {
      border: none;
      border-radius: 14px;
      padding: 14px 22px;
      font-size: 16px;
      font-weight: 600;
      cursor: pointer;
      transition: transform .05s, opacity .2s, background .2s;
      white-space: nowrap;
    }
    button:active { transform: scale(.98); }

    button.primary {
      background: #1877f2;
      color: #fff;
      min-width: 160px;
    }
    button.primary:hover { background: #166fe5; }
    button.primary:disabled { opacity: .65; cursor: not-allowed; }

    button.secondary {
      background: #e4e6eb;
      color: #1c1e21;
    }
    button.secondary:hover { background: #d8dadf; }

    /* status row */
    .status-row {
      display: flex;
      align-items: center;
      gap: 14px;
      min-height: 28px;
      margin: 10px 0 10px;
    }

    .status {
      font-size: 15px;
      color: #4b5563;
      flex: 1;
    }
    .status.ok    { color: #16a34a; }
    .status.error { color: #dc2626; }

    .eta {
      font-size: 14px;
      color: #6b7280;
      white-space: nowrap;
    }

    /* green progress bar */
    .progress-wrap {
      height: 8px;
      background: #e4e6eb;
      border-radius: 99px;
      overflow: hidden;
      margin-bottom: 22px;
      opacity: 0;
      transition: opacity .3s;
    }
    .progress-wrap.visible { opacity: 1; }

    .progress-bar {
      height: 100%;
      width: 0%;
      border-radius: 99px;
      transition: width .6s ease;
      background: linear-gradient(90deg, #16a34a, #4ade80);
    }
    .progress-bar.done {
      background: #16a34a;
      transition: width .3s ease;
    }

    /* meta grid */
    .meta {
      display: grid;
      grid-template-columns: 130px 1fr;
      gap: 8px 16px;
      margin-bottom: 20px;
      font-size: 16px;
    }
    .meta .label { font-weight: 700; color: #374151; }

    .badge {
      display: inline-block;
      font-size: 12px;
      font-weight: 700;
      padding: 2px 8px;
      border-radius: 99px;
      vertical-align: middle;
      margin-left: 6px;
    }
    .badge-whisper { background: #dcfce7; color: #15803d; }

    textarea {
      width: 100%;
      min-height: 560px;
      resize: vertical;
      border: 1px solid #ccd0d5;
      border-radius: 14px;
      padding: 20px;
      font-size: 17px;
      line-height: 1.5;
      font-family: "SFMono-Regular", Menlo, Consolas, monospace;
      background: #f7f8fa;
      color: #111827;
      outline: none;
    }
    textarea:focus {
      border-color: #1877f2;
      box-shadow: 0 0 0 3px rgba(24,119,242,.12);
    }

    .hidden { display: none !important; }

    @media (max-width: 860px) {
      .controls { flex-wrap: wrap; }
      button.primary, button.secondary, select { width: 100%; }
      .meta { grid-template-columns: 1fr; gap: 3px 0; }
      .meta .label { margin-top: 8px; }
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

    <div class="status-row">
      <div id="status" class="status"></div>
      <div id="eta" class="eta hidden"></div>
    </div>

    <div class="progress-wrap" id="progressWrap">
      <div class="progress-bar" id="progressBar"></div>
    </div>

    <div id="meta" class="meta hidden">
      <div class="label">Title</div>    <div id="metaTitle"></div>
      <div class="label">Language</div> <div id="metaLanguage"></div>
      <div class="label">Source</div>   <div id="metaSource"></div>
      <div class="label">Video ID</div> <div id="metaVideoId"></div>
    </div>

    <textarea id="transcriptBox" placeholder="Transcript will appear here..." readonly></textarea>
  </div>
</div>

<script>
  let pollTimer    = null;
  let currentJobId = null;
  let isRunning    = false;

  const $ = id => document.getElementById(id);

  function setProgress(pct, isDone) {
    const wrap = $("progressWrap");
    const bar  = $("progressBar");
    wrap.classList.add("visible");
    if (isDone) bar.classList.add("done");
    else        bar.classList.remove("done");
    bar.style.width = Math.min(pct, 100) + "%";
  }

  function hideProgress() {
    $("progressWrap").classList.remove("visible");
    $("progressBar").style.width = "0%";
  }

  function setStatus(msg, cls) {
    const el = $("status");
    el.className = "status " + (cls || "");
    el.textContent = msg;
  }

  function setEta(msg) {
    const el = $("eta");
    if (msg) { el.textContent = msg; el.classList.remove("hidden"); }
    else     { el.textContent = "";  el.classList.add("hidden");    }
  }

  function setBusy(busy) {
    isRunning = busy;
    $("getBtn").disabled      = busy;
    $("getBtn").textContent   = busy ? "Working..." : "Get Transcript";
    if (!busy) setEta(null);
  }

  function fmtSeconds(s) {
    s = Math.round(s);
    if (s < 60) return s + "s";
    return Math.floor(s / 60) + "m " + (s % 60) + "s";
  }

  const PHASE_LABELS = {
    starting:  "Starting...",
    subtitles: "Looking for subtitles...",
    audio:     "Downloading audio...",
    whisper:   "Transcribing with Whisper...",
  };

  function startPolling(jobId) {
    currentJobId = jobId;
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = setInterval(() => pollProgress(jobId), 1000);
  }

  async function pollProgress(jobId) {
    try {
      const r    = await fetch("/api/progress/" + jobId);
      const data = await r.json();
      if (!r.ok) throw new Error(data.error || "Poll error");

      setProgress(data.progress, false);

      const label = PHASE_LABELS[data.phase];
      if (label) setStatus(label);

      if (data.phase === "whisper" && data.eta_seconds != null && data.eta_seconds > 2) {
        setEta("~" + fmtSeconds(data.eta_seconds) + " remaining");
      } else {
        setEta(null);
      }

      if (data.done) {
        clearInterval(pollTimer);
        pollTimer = null;

        if (data.error) {
          hideProgress();
          setStatus("Error: " + data.error, "error");
          setBusy(false);
          return;
        }

        setProgress(100, true);
        const isWhisper = data.source === "whisper";

        $("metaTitle").textContent    = data.title;
        $("metaLanguage").textContent = data.language;
        $("metaSource").innerHTML     = data.source +
          (isWhisper ? '<span class="badge badge-whisper">Whisper AI</span>' : "");
        $("metaVideoId").textContent  = data.video_id;
        $("meta").classList.remove("hidden");
        $("transcriptBox").value = data.transcript;

        setStatus(
          isWhisper
            ? "Transcribed with Whisper (" + data.model_size + " model)."
            : "Transcript loaded successfully.",
          "ok"
        );

        setTimeout(hideProgress, 1800);
        setBusy(false);
      }
    } catch (err) {
      clearInterval(pollTimer);
      pollTimer = null;
      setStatus("Polling error: " + err.message, "error");
      setBusy(false);
    }
  }

  async function getTranscript() {
    clearPartial();
    const url   = $("urlInput").value.trim();
    const model = "base"

    if (!url) { setStatus("Please enter a YouTube URL.", "error"); return; }

    setBusy(true);
    setStatus("Starting...");
    setProgress(2, false);

    try {
      const r = await fetch("/api/transcript", {
        method:  "POST",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify({ url, model }),
      });
      const data = await r.json();
      if (!r.ok) throw new Error(data.error || "Failed to start job");
      startPolling(data.job_id);
    } catch (err) {
      hideProgress();
      setStatus("Error: " + err.message, "error");
      setBusy(false);
    }
  }

  function clearAll() {
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
    currentJobId = null;
    setBusy(false);
    hideProgress();
    $("urlInput").value   = "";
    $("status").className = "status";
    $("status").textContent = "";
    $("transcriptBox").value = "";
    $("meta").classList.add("hidden");
    ["metaTitle","metaLanguage","metaSource","metaVideoId"]
      .forEach(id => $(id).textContent = "");
    $("urlInput").focus();
  }

  function clearPartial() {
    if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
    currentJobId = null;
    setBusy(false);
    hideProgress();
    $("status").className = "status";
    $("status").textContent = "";
    $("transcriptBox").value = "";
    $("meta").classList.add("hidden");
    ["metaTitle","metaLanguage","metaSource","metaVideoId"]
      .forEach(id => $(id).textContent = "");
    $("urlInput").focus();
  }

  $("urlInput").addEventListener("keydown", e => {
    if (e.key === "Enter" && !isRunning) getTranscript();
  });

  window.onload = () => $("urlInput").focus();
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def extract_video_id(url: str) -> Optional[str]:
    patterns = [
        r"(?:v=)([a-zA-Z0-9_-]{11})",
        r"(?:youtu\.be/)([a-zA-Z0-9_-]{11})",
        r"(?:embed/)([a-zA-Z0-9_-]{11})",
        r"(?:shorts/)([a-zA-Z0-9_-]{11})",
    ]
    for pattern in patterns:
        m = re.search(pattern, url)
        if m:
            return m.group(1)
    return None


def parse_vtt(vtt_content: str) -> str:
    lines = vtt_content.splitlines()
    seen, result = [], []
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


def choose_best_lang(available: list, preferred: list) -> Optional[str]:
    if not available:
        return None
    for lang in preferred:
        if lang in available:
            return lang
    for lang in preferred:
        prefix = lang.split("-")[0]
        for av in available:
            if av == prefix or av.startswith(prefix + "-"):
                return av
    return available[0]


def list_subtitle_files(tmpdir: str) -> list:
    files = []
    for pat in ["*.vtt", "*.ttml", "*.srv3", "*.srv2", "*.srv1", "*.json3"]:
        files.extend(glob.glob(os.path.join(tmpdir, pat)))
    return files


def read_subtitle_file(path: str) -> str:
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        raw = f.read()
    if os.path.splitext(path)[1].lower() == ".vtt":
        return parse_vtt(raw)
    return html.unescape(raw).strip()


def _ydl_base_opts(use_cookies: bool) -> dict:
    opts = {
        "skip_download": True,
        "quiet":         True,
        "no_warnings":   True,
        "ignoreconfig":  True,
        "noplaylist":    True,
    }
    if use_cookies:
        opts["cookiesfrombrowser"] = ("chrome",)
    return opts


def get_video_info(url: str, use_cookies: bool = True) -> dict:
    with yt_dlp.YoutubeDL(_ydl_base_opts(use_cookies)) as ydl:
        return ydl.extract_info(url, download=False)


# ---------------------------------------------------------------------------
# Background worker
# ---------------------------------------------------------------------------

def run_job(job_id: str, url: str, video_id: str, model_size: str):
    state = get_job(job_id)
    if state is None:
        return

    state.model_size     = model_size
    preferred_langs      = ["en", "en-US", "en-GB"]

    try:
        with tempfile.TemporaryDirectory() as tmpdir:

            # ----------------------------------------------------------------
            # Phase 1 — try subtitles
            # ----------------------------------------------------------------
            state.phase    = "subtitles"
            state.progress = 5.0

            subtitle_result = None
            last_sub_err    = None

            for use_cookies in [True, False]:
                try:
                    info  = get_video_info(url, use_cookies)
                    subs  = info.get("subtitles") or {}
                    autos = info.get("automatic_captions") or {}

                    lang = choose_best_lang(list(subs.keys()), preferred_langs)
                    src  = "manual subtitles"
                    if lang is None:
                        lang = choose_best_lang(list(autos.keys()), preferred_langs)
                        src  = "automatic captions"

                    if lang is None:
                        raise RuntimeError("No subtitles or automatic captions found.")

                    dl_opts = {
                        **_ydl_base_opts(use_cookies),
                        "skip_download":     True,
                        "writesubtitles":    src == "manual subtitles",
                        "writeautomaticsub": src == "automatic captions",
                        "subtitleslangs":    [lang],
                        "subtitlesformat":   "vtt/best",
                        "outtmpl":           os.path.join(tmpdir, "transcript.%(ext)s"),
                    }
                    with yt_dlp.YoutubeDL(dl_opts) as ydl:
                        ydl.extract_info(url, download=True)

                    files = list_subtitle_files(tmpdir)
                    if not files:
                        raise RuntimeError("No subtitle file found after download.")

                    text = read_subtitle_file(files[0])
                    if not text.strip():
                        raise RuntimeError("Subtitle file parsed as empty.")

                    subtitle_result = (text, lang, info.get("title", video_id), src)
                    break

                except Exception as e:
                    last_sub_err = e

            if subtitle_result:
                text, lang, title, src = subtitle_result
                state.progress   = 100.0
                state.transcript = text
                state.language   = lang
                state.title      = title
                state.source     = src
                state.video_id   = video_id
                state.phase      = "done"
                state.done       = True
                return

            # ----------------------------------------------------------------
            # Phase 2 — download audio
            # ----------------------------------------------------------------
            state.phase    = "audio"
            state.progress = 15.0

            audio_info  = None
            audio_file  = None
            last_dl_err = None

            for use_cookies in [True, False]:
                try:
                    audio_path = os.path.join(tmpdir, "audio.%(ext)s")
                    dl_opts    = {
                        "format":       "bestaudio/best",
                        "quiet":        True,
                        "no_warnings":  True,
                        "ignoreconfig": True,
                        "noplaylist":   True,
                        "outtmpl":      audio_path,
                        "postprocessors": [{
                            "key":              "FFmpegExtractAudio",
                            "preferredcodec":   "mp3",
                            "preferredquality": "128",
                        }],
                    }
                    if use_cookies:
                        dl_opts["cookiesfrombrowser"] = ("chrome",)

                    with yt_dlp.YoutubeDL(dl_opts) as ydl:
                        audio_info = ydl.extract_info(url, download=True)

                    found = glob.glob(os.path.join(tmpdir, "audio.*"))
                    if not found:
                        raise RuntimeError("Audio file not found after download.")
                    audio_file = found[0]
                    break

                except Exception as e:
                    last_dl_err = e

            if audio_file is None:
                raise RuntimeError(
                    f"Could not obtain a transcript.\n"
                    f"  Subtitle error: {last_sub_err}\n"
                    f"  Audio download error: {last_dl_err}"
                )

            state.progress       = 35.0
            duration             = float((audio_info or {}).get("duration") or 0)
            state.audio_duration = duration

            # ----------------------------------------------------------------
            # Phase 3 — Whisper transcription
            # ----------------------------------------------------------------
            state.phase         = "whisper"
            state.whisper_start = time.monotonic()

            model  = get_whisper_model(model_size)
            result = model.transcribe(audio_file, verbose=False)

            text = (result.get("text") or "").strip()
            if not text:
                raise RuntimeError("Whisper returned an empty transcription.")

            lang  = result.get("language", "unknown")
            title = (audio_info or {}).get("title", video_id)

            state.progress   = 100.0
            state.transcript = text
            state.language   = lang
            state.title      = title
            state.source     = "whisper"
            state.video_id   = video_id
            state.phase      = "done"
            state.done       = True

    except Exception as exc:
        state.error = str(exc)
        state.phase = "error"
        state.done  = True


# ---------------------------------------------------------------------------
# Flask routes
# ---------------------------------------------------------------------------

@app.route("/", methods=["GET"])
def index():
    return render_template_string(HTML_PAGE)


@app.route("/api/transcript", methods=["POST"])
def api_transcript():
    """Start a transcript job and return the job_id immediately."""
    data  = request.get_json(silent=True) or {}
    url   = (data.get("url") or "").strip()
    model = (data.get("model") or DEFAULT_MODEL).strip()

    if model not in WHISPER_MODELS:
        model = DEFAULT_MODEL

    if not url:
        return jsonify({"error": "No URL provided."}), 400

    video_id = extract_video_id(url)
    if not video_id:
        return jsonify({"error": "Could not extract a valid YouTube video ID."}), 400

    cleanup_old_jobs()
    job_id, _state = new_job()

    threading.Thread(
        target=run_job,
        args=(job_id, url, video_id, model),
        daemon=True,
    ).start()

    return jsonify({"job_id": job_id})


@app.route("/api/progress/<job_id>", methods=["GET"])
def api_progress(job_id: str):
    """Poll endpoint — returns current progress + result when done."""
    state = get_job(job_id)
    if state is None:
        return jsonify({"error": "Job not found."}), 404

    # Compute live Whisper progress & ETA
    if state.phase == "whisper" and state.audio_duration > 0 and state.whisper_start > 0:
        elapsed   = time.monotonic() - state.whisper_start
        factor    = WHISPER_MODELS.get(state.model_size, {}).get("factor", 1.0)
        estimate  = state.audio_duration * factor      # total expected seconds
        pct       = 35.0 + min(elapsed / max(estimate, 1), 1.0) * 60.0   # 35 -> 95
        state.progress = min(pct, 95.0)
        remaining = max(estimate - elapsed, 0)
        eta_secs  = round(remaining, 0) if remaining > 2 else None
    else:
        eta_secs = None

    resp = {
        "phase":      state.phase,
        "progress":   round(state.progress, 1),
        "done":       state.done,
        "model_size": state.model_size,
        "eta_seconds": eta_secs,
    }

    if state.done:
        if state.error:
            resp["error"] = state.error
        else:
            resp.update({
                "transcript": state.transcript,
                "title":      state.title,
                "language":   state.language,
                "source":     state.source,
                "video_id":   state.video_id,
            })

    return jsonify(resp)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def open_browser():
    time.sleep(1.0)
    webbrowser.open("http://127.0.0.1:5001")


if __name__ == "__main__":
    threading.Thread(target=open_browser, daemon=True).start()
    app.run(host="127.0.0.1", port=5001, debug=False, use_reloader=False, threaded=True)