#!/usr/bin/env python3
"""vidgrab: resolve a TikTok / YouTube / Douyin / X video link and download it.

Made to be called by an AI agent. One link (or a whole share text) in, one JSON line out.

    vidgrab.py <link or share text>      resolve + download
    vidgrab.py resolve <link>            resolve only (no download)
    vidgrab.py clean                     delete downloads older than VIDGRAB_KEEP_DAYS

Resolving uses Apify actors (needs APIFY_TOKEN). Python 3.8+, standard library only;
ffmpeg is optional (used to merge YouTube's separate HD video and audio streams).
Every run also deletes this tool's own downloads older than 7 days.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

__version__ = "0.1.0"

APIFY_API = "https://api.apify.com/v2/acts/{actor}/run-sync-get-dataset-items"
# 256 MB is plenty for these actors. Apify charges the per-run start fee per GB of memory:
# the actors' 2 GB default cost about $0.18 per video, 256 MB about $0.0001.
APIFY_MEMORY_MB = 256
APIFY_TIMEOUT_S = 180

ACTOR_DOUYIN = "easyapi~douyin-video-downloader"
ACTOR_GENERIC = "easyapi~all-in-one-media-downloader"

PLATFORMS = {
    "douyin": ("douyin.com", "iesdouyin.com"),
    "tiktok": ("tiktok.com",),
    "youtube": ("youtube.com", "youtu.be"),
    "x": ("x.com", "twitter.com"),
}

FILE_PREFIX = "vidgrab-"
DEFAULT_KEEP_DAYS = 7
DEFAULT_MAX_HEIGHT = 1080
# Ranged requests of this size: YouTube throttles a single whole-file request to roughly
# playback speed (~100 KB/s measured), but serves 10 MB ranges at full speed.
CHUNK_BYTES = 10 << 20
USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")


class VidgrabError(Exception):
    def __init__(self, stage, message):
        super().__init__(message)
        self.stage = stage


# ---------------------------------------------------------------- config

def load_dotenv(path):
    """KEY=VALUE lines from a .env file next to the script; never overrides the real environment."""
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
    except FileNotFoundError:
        pass


def download_dir():
    return os.path.expanduser(os.environ.get("VIDGRAB_DIR") or "~/vidgrab")


def keep_days():
    try:
        return max(1, int(os.environ.get("VIDGRAB_KEEP_DAYS", DEFAULT_KEEP_DAYS)))
    except ValueError:
        return DEFAULT_KEEP_DAYS


# ---------------------------------------------------------------- link handling

URL_RE = re.compile(r"https?://[^\s\u3000-\u303f\u4e00-\u9fff\uff00-\uffef]+", re.I)


def extract_url(text):
    """First link in a share text such as '4.82 复制打开抖音… https://v.douyin.com/xxx/ 复制此链接…'."""
    m = URL_RE.search(text or "")
    if not m:
        raise VidgrabError("input", "no link found")
    return m.group(0).rstrip(".,;:!?)]}>'\"")


def detect_platform(url):
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    for name, domains in PLATFORMS.items():
        if any(host == d or host.endswith("." + d) for d in domains):
            return name
    return "other"


def actor_for(platform):
    return ACTOR_DOUYIN if platform == "douyin" else ACTOR_GENERIC


def actor_input(actor, url):
    if actor == ACTOR_GENERIC:
        return {"link": url, "proxyConfiguration": {"useApifyProxy": False}}
    return {"links": [url], "proxyConfiguration": {"useApifyProxy": False}}


# ---------------------------------------------------------------- Apify

def call_apify(actor, payload, token):
    if not token:
        raise VidgrabError("config", "APIFY_TOKEN is not set (environment variable or .env next to vidgrab.py)")
    query = urllib.parse.urlencode({"memory": APIFY_MEMORY_MB, "timeout": APIFY_TIMEOUT_S})
    req = urllib.request.Request(
        APIFY_API.format(actor=actor) + "?" + query,
        data=json.dumps(payload).encode(),
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=APIFY_TIMEOUT_S + 30) as res:
            return json.loads(res.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            body = json.loads(e.read().decode("utf-8"))
            detail = (body.get("error") or {}).get("message") or ""
        except Exception:
            pass
        raise VidgrabError("resolve", "Apify HTTP %d%s" % (e.code, ": " + detail if detail else ""))
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise VidgrabError("resolve", "Apify unreachable: %s" % e)
    except ValueError:
        raise VidgrabError("resolve", "Apify returned non-JSON")


def _int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0


def _missing(v):
    return v is None or str(v).strip().lower() in ("", "none", "null")


def normalize_media(m):
    """One entry of the actor's `medias` list → a flat dict we can rank."""
    quality = str(m.get("quality") or m.get("label") or "")
    mime = str(m.get("mimeType") or "")
    kind = "audio" if str(m.get("type") or "").lower() == "audio" else "video"
    height = _int(m.get("height"))
    if not height:
        found = re.search(r"(\d{3,4})p", quality)
        height = int(found.group(1)) if found else 0
    if kind == "audio":
        has_audio = True
    elif not _missing(m.get("audioQuality")) or str(m.get("is_audio")).lower() == "true" or "mp4a" in mime:
        has_audio = True
    elif mime or "audioQuality" in m:
        has_audio = False  # YouTube-style entry that says it has no audio track: video-only stream
    else:
        has_audio = True   # Douyin / TikTok entries carry no stream details and are complete videos
    q = quality.lower()
    return {
        "url": m.get("url"),
        "kind": kind,
        "height": height,
        "ext": str(m.get("extension") or m.get("ext") or "").lower(),
        "quality": quality,
        "mime": mime,
        "has_audio": has_audio,
        "watermark": "watermark" in q and "no_watermark" not in q,
        "h264": "avc1" in mime or (not mime and kind == "video"),
        "aac": "mp4a" in mime if mime else str(m.get("extension") or m.get("ext") or "").lower() in ("m4a", "mp4"),
        "bitrate": _int(m.get("bitrate")),
    }


def parse_result(data):
    """Actor output → (meta, media list). Raises when the actor reported an error or found nothing."""
    item = data[0] if isinstance(data, list) and data else data
    if not isinstance(item, dict):
        raise VidgrabError("resolve", "Apify returned no result")
    result = item.get("result") if isinstance(item.get("result"), dict) else item
    err = result.get("error", item.get("error"))
    if err and str(err).lower() not in ("false", "0", "none"):
        raise VidgrabError("resolve", "resolver error: %s" % (err if isinstance(err, str) else json.dumps(err)))
    medias = []
    for m in result.get("medias") or []:
        if isinstance(m, dict) and str(m.get("url") or "").startswith("http"):
            medias.append(normalize_media(m))
        for f in (m.get("formats") or []) if isinstance(m, dict) else []:
            if isinstance(f, dict) and str(f.get("url") or "").startswith("http"):
                medias.append(normalize_media(dict(f, type=m.get("type", "video"))))
    if not medias:
        raise VidgrabError("resolve", "resolved, but no downloadable media was returned")
    meta = {
        "title": result.get("title") or "",
        "author": result.get("author") or "",
        "id": str(result.get("id") or ""),
        "thumbnail": result.get("thumbnail") or "",
    }
    return meta, medias


def choose(medias, max_height=DEFAULT_MAX_HEIGHT, can_merge=False):
    """Pick what to download.

    Returns {"video": media, "audio": media or None, "note": str}. With an audio entry the two
    streams must be merged (ffmpeg). Preference: no watermark, then height ≤ max_height (highest),
    then H.264/AAC mp4 for phones, then bitrate.
    """
    videos = [m for m in medias if m["kind"] == "video"]
    audios = [m for m in medias if m["kind"] == "audio"]
    if not videos:
        raise VidgrabError("resolve", "only audio was returned")

    def fits(m):
        return not m["height"] or m["height"] <= max_height

    def rank(m):
        return (not m["watermark"], fits(m), m["height"] if fits(m) else -m["height"],
                m["h264"], m["ext"] == "mp4", m["bitrate"])

    complete = sorted((m for m in videos if m["has_audio"]), key=rank, reverse=True)
    video_only = sorted((m for m in videos if not m["has_audio"]), key=rank, reverse=True)
    best_complete = complete[0] if complete else None

    if can_merge and video_only and audios:
        v = video_only[0]
        if fits(v) and (best_complete is None or v["height"] > best_complete["height"]):
            # AAC (plays everywhere, fits mp4); ~128 kb/s is plenty and a third of the 390 kb/s stream
            a = sorted(audios, key=lambda m: (m["aac"], m["bitrate"] <= 160000, m["bitrate"]), reverse=True)[0]
            return {"video": v, "audio": a, "note": ""}
    if best_complete:
        note = ""
        if video_only and not can_merge and video_only[0]["height"] > best_complete["height"]:
            note = "higher quality needs ffmpeg (separate video/audio streams); downloaded %sp" % best_complete["height"]
        return {"video": best_complete, "audio": None, "note": note}
    return {"video": video_only[0], "audio": None, "note": "no stream with audio was available: video has no sound"}


def resolve(text, token, max_height=DEFAULT_MAX_HEIGHT, can_merge=None):
    url = extract_url(text)
    platform = detect_platform(url)
    actor = actor_for(platform)
    data = call_apify(actor, actor_input(actor, url), token)
    meta, medias = parse_result(data)
    if can_merge is None:
        can_merge = shutil.which("ffmpeg") is not None
    pick = choose(medias, max_height=max_height, can_merge=can_merge)
    return {"platform": platform, "source_url": url, "meta": meta, "pick": pick}


# ---------------------------------------------------------------- download

def _range_total(content_range):
    """'bytes 0-1023/52428800' → 52428800 (None when unknown)."""
    m = re.search(r"/(\d+)\s*$", content_range or "")
    return int(m.group(1)) if m else None


def fetch_to(url, path, referer=None, chunk=CHUNK_BYTES):
    """Download in `chunk`-sized ranges (falls back to one request if the server ignores Range)."""
    headers = {"User-Agent": USER_AGENT}
    if referer:
        headers["Referer"] = referer
    tmp = path + ".part"
    try:
        with open(tmp, "wb") as out:
            start = 0
            while True:
                req = urllib.request.Request(url, headers=dict(headers, Range="bytes=%d-%d" % (start, start + chunk - 1)))
                try:
                    res = urllib.request.urlopen(req, timeout=60)
                except urllib.error.HTTPError as e:
                    if e.code == 416 and start > 0:  # asked past the end: done
                        break
                    raise
                with res:
                    if res.status == 200:  # Range ignored: the whole file is coming
                        out.seek(0)
                        out.truncate()
                        shutil.copyfileobj(res, out, 1 << 20)
                        break
                    total = _range_total(res.headers.get("Content-Range"))
                    got = 0
                    while True:
                        block = res.read(1 << 20)
                        if not block:
                            break
                        out.write(block)
                        got += len(block)
                start += got
                if got < chunk or (total is not None and start >= total):
                    break
        os.replace(tmp, path)
    except urllib.error.HTTPError as e:
        _unlink(tmp)
        raise VidgrabError("download", "HTTP %d from %s" % (e.code, urllib.parse.urlparse(url).hostname))
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        _unlink(tmp)
        raise VidgrabError("download", "download failed: %s" % e)
    if os.path.getsize(path) == 0:
        _unlink(path)
        raise VidgrabError("download", "downloaded file is empty")


def _unlink(path):
    try:
        os.remove(path)
    except OSError:
        pass


def _slug(s, limit=40):
    s = re.sub(r"[^\w\-]+", "-", s, flags=re.UNICODE).strip("-")
    return s[:limit] or "video"


def download(resolved, directory):
    os.makedirs(directory, exist_ok=True)
    pick = resolved["pick"]
    video, audio = pick["video"], pick["audio"]
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    base = "%s%s-%s-%s" % (FILE_PREFIX, stamp, resolved["platform"], _slug(resolved["meta"]["id"] or resolved["meta"]["title"]))
    referer = resolved["source_url"]

    if audio is None:
        ext = video["ext"] if video["ext"] in ("mp4", "webm", "mov", "m4v") else "mp4"
        path = os.path.join(directory, base + "." + ext)
        fetch_to(video["url"], path, referer)
        return path

    v_path = os.path.join(directory, base + ".video." + (video["ext"] or "mp4"))
    a_path = os.path.join(directory, base + ".audio." + (audio["ext"] or "m4a"))
    out_ext = "mp4" if video["h264"] and audio["aac"] else "mkv"
    path = os.path.join(directory, base + "." + out_ext)
    try:
        fetch_to(video["url"], v_path, referer)
        fetch_to(audio["url"], a_path, referer)
        proc = subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", v_path, "-i", a_path,
             "-map", "0:v:0", "-map", "1:a:0", "-c", "copy", path],
            capture_output=True, text=True, timeout=600)
        if proc.returncode != 0 or not os.path.exists(path):
            _unlink(path)
            raise VidgrabError("download", "ffmpeg merge failed: %s" % (proc.stderr.strip()[-300:] or proc.returncode))
    finally:
        _unlink(v_path)
        _unlink(a_path)
    return path


# ---------------------------------------------------------------- cleanup

def clean(directory, days, now=None):
    """Delete this tool's files (vidgrab-*) older than `days`. Never touches anything else."""
    if not os.path.isdir(directory):
        return []
    cutoff = (now or time.time()) - days * 86400
    removed = []
    for name in os.listdir(directory):
        if not name.startswith(FILE_PREFIX):
            continue
        path = os.path.join(directory, name)
        try:
            if os.path.isfile(path) and os.path.getmtime(path) < cutoff:
                os.remove(path)
                removed.append(name)
        except OSError:
            pass
    return removed


# ---------------------------------------------------------------- CLI

USAGE = """usage:
  vidgrab.py <link or share text> [--max-height N]   resolve + download (prints one JSON line)
  vidgrab.py resolve <link or share text>            resolve only
  vidgrab.py clean                                   delete downloads older than VIDGRAB_KEEP_DAYS (default 7)
  vidgrab.py --version

env: APIFY_TOKEN (required), VIDGRAB_DIR (default ~/vidgrab), VIDGRAB_KEEP_DAYS (default 7)"""


def emit(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
    if not argv or argv[0] in ("-h", "--help"):
        print(USAGE)
        return 0 if argv else 2
    if argv[0] == "--version":
        print(__version__)
        return 0

    directory, days = download_dir(), keep_days()
    if argv[0] == "clean":
        emit({"ok": True, "removed": clean(directory, days), "dir": directory, "keep_days": days})
        return 0

    max_height = DEFAULT_MAX_HEIGHT
    if "--max-height" in argv:
        i = argv.index("--max-height")
        try:
            max_height = int(argv[i + 1])
        except (IndexError, ValueError):
            print(USAGE, file=sys.stderr)
            return 2
        del argv[i:i + 2]
    only_resolve = argv[0] == "resolve"
    text = " ".join(argv[1:] if only_resolve else argv)

    clean(directory, days)  # housekeeping on every run: old downloads never pile up
    try:
        r = resolve(text, os.environ.get("APIFY_TOKEN", "").strip(), max_height=max_height)
        pick = r["pick"]
        out = {
            "ok": True,
            "platform": r["platform"],
            "title": r["meta"]["title"],
            "author": r["meta"]["author"],
            "source_url": r["source_url"],
            "play_url": pick["video"]["url"],
            "quality": "%sp" % pick["video"]["height"] if pick["video"]["height"] else pick["video"]["quality"],
        }
        if pick["audio"]:
            out["audio_url"] = pick["audio"]["url"]
        if not only_resolve:
            path = download(r, directory)
            out["file"] = path
            out["size"] = os.path.getsize(path)
            out["expires"] = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")
        if pick["note"]:
            out["note"] = pick["note"]
        emit(out)
        return 0
    except VidgrabError as e:
        emit({"ok": False, "stage": e.stage, "error": str(e)})
        return 1


if __name__ == "__main__":
    sys.exit(main())
