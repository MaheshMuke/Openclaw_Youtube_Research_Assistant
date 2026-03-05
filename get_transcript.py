#!/usr/bin/env python3

import argparse
import sys
import re
import subprocess
import tempfile
import threading
import shutil
import json
import time
from pathlib import Path
from datetime import datetime

# --------------------------------
# Paths
# --------------------------------
DATA_DIR = Path.home() / ".openclaw/workspace/skills/youtube-watcher/data"
INDEX_FILE = DATA_DIR / "index.json"

# --------------------------------
# Dependency check
# --------------------------------
def check_dependencies():

    yt_dlp_path = shutil.which("yt-dlp")

    if not yt_dlp_path:
        print("❌ yt-dlp not installed. Run: pip install yt-dlp", file=sys.stderr)
        sys.exit(1)

    print(f"✅ yt-dlp found: {yt_dlp_path}", file=sys.stderr)

    try:
        version = subprocess.run(
            ["yt-dlp", "--version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=5
        ).stdout.decode().strip()

        print(f"✅ yt-dlp version: {version}", file=sys.stderr)

    except Exception:
        pass


# --------------------------------
# Extract video ID
# --------------------------------
def extract_video_id(url):

    patterns = [
        r"(?:v=|\/)([0-9A-Za-z_-]{11})",
        r"youtu\.be\/([0-9A-Za-z_-]{11})",
        r"embed\/([0-9A-Za-z_-]{11})"
    ]

    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)

    print("❌ Invalid YouTube URL", file=sys.stderr)
    sys.exit(1)


# --------------------------------
# Cleanup old transcripts (24h)
# --------------------------------
def cleanup_old():

    if not DATA_DIR.exists():
        return

    cutoff = time.time() - 86400

    for f in DATA_DIR.glob("*.txt"):
        if f.stat().st_mtime < cutoff:
            f.unlink()


# --------------------------------
# Save transcript
# --------------------------------
def save_transcript(video_id, transcript, url):

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    path = DATA_DIR / f"{video_id}.txt"

    path.write_text(transcript, encoding="utf-8")

    index = {}

    if INDEX_FILE.exists():
        try:
            index = json.loads(INDEX_FILE.read_text())
        except:
            pass

    index[video_id] = {
        "url": url,
        "saved": datetime.now().isoformat(),
        "lines": transcript.count("\n")
    }

    tmp = INDEX_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(index, indent=2))
    tmp.replace(INDEX_FILE)

    return path


# --------------------------------
# Load transcript
# --------------------------------
def load_transcript(video_id):

    path = DATA_DIR / f"{video_id}.txt"

    if not path.exists():
        return None

    return path.read_text(encoding="utf-8")


# --------------------------------
# Fetch subtitles using yt-dlp
# --------------------------------
def fetch_subtitles(url, lang="en"):

    with tempfile.TemporaryDirectory() as temp_dir:

        cmd = [
            "yt-dlp",
            "--extractor-args", "youtube:player_client=android",
            "--skip-download",
            "--write-subs",
            "--write-auto-subs",
            "--sub-langs", lang,
            "--sub-format", "vtt",
            "--convert-subs", "vtt",
            "--no-playlist",
            "--no-write-info-json",
            "--no-write-playlist-metafiles",
            "--force-ipv4",
            "--retries", "3",
            "--fragment-retries", "3",
            "--sleep-requests", "1",
            "--no-check-certificates",
            "--output", "subs",
            url
        ]

        stderr_lines = []

        def read_err(proc):

            for line in proc.stderr:
                decoded = line.decode(errors="replace").rstrip()
                stderr_lines.append(decoded)

        proc = subprocess.Popen(
            cmd,
            cwd=temp_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )

        t = threading.Thread(target=read_err, args=(proc,), daemon=True)
        t.start()

        try:
            proc.wait(timeout=60)

        except subprocess.TimeoutExpired:

            proc.kill()
            print("❌ yt-dlp timeout", file=sys.stderr)
            return None

        if proc.returncode != 0:
            print("❌ yt-dlp failed", file=sys.stderr)
            return None

        vtt_files = list(Path(temp_dir).glob("*.vtt"))

        if not vtt_files:
            print("❌ No subtitles found", file=sys.stderr)
            return None

        content = vtt_files[0].read_text(encoding="utf-8")

        return clean_vtt(content)


# --------------------------------
# Clean VTT
# --------------------------------
def clean_vtt(content):

    lines = content.splitlines()

    result = []

    seen = set()

    current_time = None

    for line in lines:

        line = line.strip()

        if not line or line == "WEBVTT" or line.isdigit():
            continue

        if "-->" in line:

            start = line.split(" --> ")[0]

            parts = start.split(":")

            if len(parts) == 3:
                _, m, s = parts
            else:
                m, s = parts

            current_time = f"[{m}:{s[:2]}]"

            continue

        clean = re.sub(r"<[^>]+>", "", line)
        clean = clean.replace("&nbsp;", " ").strip()

        if clean and current_time:

            if clean not in seen:

                seen.add(clean)

                result.append(f"{current_time} {clean}")

    return "\n".join(result[:10000])


# --------------------------------
# Chunk transcript (line safe)
# --------------------------------
def chunk_text(text, size=40):

    lines = text.split("\n")

    chunks = []

    for i in range(0, len(lines), size):
        chunks.append("\n".join(lines[i:i+size]))

    return chunks


# --------------------------------
# Retrieve relevant chunks
# --------------------------------
def retrieve_chunks(question, chunks):

    q_words = set(re.findall(r"\w+", question.lower()))

    scored = []

    for chunk in chunks:

        c_words = set(re.findall(r"\w+", chunk.lower()))

        score = len(q_words & c_words)

        if score > 0:
            scored.append((score, chunk))

    scored.sort(reverse=True)

    return [c for _, c in scored[:3]]


# --------------------------------
# Answer question
# --------------------------------
def answer(video_id, question):

    transcript = load_transcript(video_id)

    if not transcript:
        print("❌ Transcript not stored for this video", file=sys.stderr)
        sys.exit(1)

    chunks = chunk_text(transcript)

    relevant = retrieve_chunks(question, chunks)

    if not relevant:
        print("This topic is not covered in the video.")
        return

    for c in relevant:
        print(c)
        print()


# --------------------------------
# Main
# --------------------------------
def main():

    parser = argparse.ArgumentParser()

    sub = parser.add_subparsers(dest="cmd")

    f = sub.add_parser("fetch")
    f.add_argument("url")

    q = sub.add_parser("ask")
    q.add_argument("video_id")
    q.add_argument("question")

    sub.add_parser("list")

    args = parser.parse_args()

    if args.cmd == "fetch":

        check_dependencies()
        cleanup_old()

        video_id = extract_video_id(args.url)

        transcript = fetch_subtitles(args.url)

        if not transcript:
            print("❌ Could not fetch transcript")
            sys.exit(1)

        save_transcript(video_id, transcript, args.url)

        print(transcript)

    elif args.cmd == "ask":

        answer(args.video_id, args.question)

    elif args.cmd == "list":

        if not INDEX_FILE.exists():
            print("No transcripts stored")
            return

        index = json.loads(INDEX_FILE.read_text())

        for vid in index:
            print(vid, index[vid]["url"])


if __name__ == "__main__":
    main()
