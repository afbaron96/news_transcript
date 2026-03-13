import re
import threading
import time
import webbrowser
import urllib.request
import html
import ssl
import certifi
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
*{box-sizing:border-box}

body{
margin:0;
font-family:Helvetica,Arial,sans-serif;
background:#f0f2f5;
color:#1c1e21;
}

.page{
max-width:1320px;
margin:24px auto;
padding:0 16px;
}

.card{
background:#ffffff;
border-radius:18px;
padding:28px 32px;
box-shadow:0 1px 2px rgba(0,0,0,0.08),0 8px 24px rgba(0,0,0,0.06);
border:1px solid #e4e6eb;
}

h1{
margin:0 0 6px 0;
font-size:34px;
font-weight:700;
color:#1f2937;
}

.controls{
display:flex;
gap:16px;
align-items:center;
margin-bottom:18px;
}

input{
flex:1;
padding:15px 18px;
font-size:16px;
border:1px solid #ccd0d5;
border-radius:14px;
}

button{
border:none;
border-radius:14px;
padding:15px 22px;
font-size:16px;
font-weight:600;
cursor:pointer;
}

button.primary{
background:#1877f2;
color:white;
}

button.secondary{
background:#e4e6eb;
}

.status{
margin:10px 0 20px 0;
color:#444;
}

.meta{
display:grid;
grid-template-columns:140px 1fr;
gap:10px 18px;
margin-bottom:22px;
font-size:16px;
}

.meta .label{
font-weight:700;
}

textarea{
width:100%;
min-height:580px;
resize:vertical;
border:1px solid #ccd0d5;
border-radius:14px;
padding:20px;
font-size:16px;
font-family:Menlo,Consolas,monospace;
background:#f7f8fa;
}

.hidden{display:none}
</style>
</head>

<body>

<div class="page">
<div class="card">

<h1>Jess' Transcript Extractor</h1>

<div class="controls">
<input id="urlInput" placeholder="Paste YouTube URL here..." />
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

function setLoading(v){
const btn=document.getElementById("getBtn")

if(v){
btn.disabled=true
btn.textContent="Loading..."
}else{
btn.disabled=false
btn.textContent="Get Transcript"
}
}

async function getTranscript(){

const url=document.getElementById("urlInput").value.trim()
const status=document.getElementById("status")
const box=document.getElementById("transcriptBox")
const meta=document.getElementById("meta")

if(!url){
status.textContent="Please enter a YouTube URL."
return
}

setLoading(true)

try{

const res=await fetch("/api/transcript",{
method:"POST",
headers:{"Content-Type":"application/json"},
body:JSON.stringify({url})
})

const data=await res.json()

if(!res.ok) throw new Error(data.error)

document.getElementById("metaTitle").textContent=data.title
document.getElementById("metaLanguage").textContent=data.language
document.getElementById("metaSource").textContent=data.source
document.getElementById("metaVideoId").textContent=data.video_id

meta.classList.remove("hidden")

box.value=data.transcript
status.textContent="Transcript loaded successfully."

}catch(e){
status.textContent="Error: "+e.message
}

setLoading(false)
}

function clearAll(){

document.getElementById("urlInput").value=""
document.getElementById("status").textContent=""
document.getElementById("transcriptBox").value=""
document.getElementById("meta").classList.add("hidden")

}

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


def choose_best_format(formats):

    preferred_exts = ["vtt", "ttml", "srv3", "srv2", "srv1", "json3"]

    for ext in preferred_exts:
        for fmt in formats:
            if fmt.get("ext") == ext and fmt.get("url"):
                return fmt

    for fmt in formats:
        if fmt.get("url"):
            return fmt

    raise RuntimeError("No usable subtitle format found")


def pick_subtitle_track(tracks, langs):

    if not tracks:
        return None

    for lang in langs:
        if lang in tracks and tracks[lang]:
            return lang, choose_best_format(tracks[lang])

    for lang in langs:
        prefix = lang.split("-")[0]
        for available, formats in tracks.items():
            if available.startswith(prefix):
                return available, choose_best_format(formats)

    for available, formats in tracks.items():
        if formats:
            return available, choose_best_format(formats)

    return None


def download_text(url: str) -> str:

    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0"
        },
    )

    ssl_context = ssl.create_default_context(cafile=certifi.where())

    with urllib.request.urlopen(req, timeout=30, context=ssl_context) as resp:
        charset = resp.headers.get_content_charset() or "utf-8"
        return resp.read().decode(charset, errors="replace")


def fetch_transcript(url, video_id):

    preferred_langs = ["en", "en-US", "en-GB"]

    opts = {
        "skip_download": True,
        "quiet": True,
        "no_warnings": True,
        "ignoreconfig": True,
    }

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)

    title = info.get("title", video_id)

    subtitles = info.get("subtitles") or {}
    automatic = info.get("automatic_captions") or {}

    selected = pick_subtitle_track(subtitles, preferred_langs)
    source = "manual subtitles"

    if selected is None:
        selected = pick_subtitle_track(automatic, preferred_langs)
        source = "automatic captions"

    if selected is None:
        raise RuntimeError("No subtitles available")

    lang, fmt = selected

    raw = download_text(fmt["url"])

    if fmt.get("ext") == "vtt":
        text = parse_vtt(raw)
    else:
        text = html.unescape(raw)

    return text, lang, title, source


@app.route("/")
def index():
    return render_template_string(HTML_PAGE)


@app.route("/api/transcript", methods=["POST"])
def api():

    data = request.get_json()

    url = data.get("url", "").strip()

    if not url:
        return jsonify({"error": "No URL provided"}), 400

    video_id = extract_video_id(url)

    if not video_id:
        return jsonify({"error": "Invalid YouTube URL"}), 400

    try:

        transcript, lang, title, source = fetch_transcript(url, video_id)

        return jsonify(
            {
                "title": title,
                "language": lang,
                "source": source,
                "video_id": video_id,
                "transcript": transcript,
            }
        )

    except Exception as e:

        return jsonify({"error": str(e)}), 500


def open_browser():
    time.sleep(1)
    webbrowser.open("http://127.0.0.1:5000")


if __name__ == "__main__":
    threading.Thread(target=open_browser, daemon=True).start()
    app.run(host="127.0.0.1", port=5000, debug=False, use_reloader=False)