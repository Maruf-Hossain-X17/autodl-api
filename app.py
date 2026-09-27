import os
import re
import ssl
import time
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from urllib.parse import urlparse

import certifi
from flask import Flask, request, jsonify
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import yt_dlp

# ---------------------------------------------------------------------------
# SSL Configuration (Secure default with Certifi CA Bundle)
# ---------------------------------------------------------------------------
os.environ['SSL_CERT_FILE'] = certifi.where()
os.environ['REQUESTS_CA_BUNDLE'] = certifi.where()

# ---------------------------------------------------------------------------
# App / Logging setup
# ---------------------------------------------------------------------------
app = Flask(__name__)

ALLOWED_ORIGINS = os.environ.get("ALLOWED_ORIGINS", "*")
CORS(app, resources={r"/api/*": {"origins": ALLOWED_ORIGINS}, r"/alldl": {"origins": ALLOWED_ORIGINS}})

limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=[os.environ.get("RATE_LIMIT", "30 per minute")],
    storage_uri="memory://",
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("video-api")

log.info(f"yt-dlp version: {yt_dlp.version.__version__}")

API_KEY = os.environ.get("API_KEY")
EXTRACT_TIMEOUT = int(os.environ.get("EXTRACT_TIMEOUT", "30"))
executor = ThreadPoolExecutor(max_workers=int(os.environ.get("MAX_WORKERS", "16")))

# ---------------------------------------------------------------------------
# Domain allow-list
# ---------------------------------------------------------------------------
ALLOWED_DOMAINS = [
    "youtube.com", "youtu.be", "m.youtube.com",
    "instagram.com", "www.instagram.com",
    "tiktok.com", "www.tiktok.com", "vm.tiktok.com", "vt.tiktok.com",
    "facebook.com", "www.facebook.com", "fb.watch", "m.facebook.com",
    "twitter.com", "x.com",
    "pinterest.com", "www.pinterest.com", "pin.it",
]

URL_RE = re.compile(r"^https?://", re.IGNORECASE)

# ---------------------------------------------------------------------------
# In-memory TTL cache
# ---------------------------------------------------------------------------
CACHE_TTL = int(os.environ.get("CACHE_TTL", "300"))
_cache = {}
_cache_lock = threading.Lock()


def cache_get(key):
    with _cache_lock:
        item = _cache.get(key)
        if not item:
            return None
        value, expires_at = item
        if time.time() > expires_at:
            _cache.pop(key, None)
            return None
        return value


def cache_set(key, value):
    with _cache_lock:
        _cache[key] = (value, time.time() + CACHE_TTL)


def is_allowed_url(url: str) -> bool:
    if not URL_RE.match(url):
        return False
    try:
        host = urlparse(url).hostname or ""
    except ValueError:
        return False
    host = host.lower()
    return any(host == d or host.endswith("." + d) for d in ALLOWED_DOMAINS)


# ---------------------------------------------------------------------------
# Cookie files
# ---------------------------------------------------------------------------
COOKIE_DIR = os.environ.get("COOKIE_DIR", ".")

COOKIE_MAP = {
    "instagram.com": "insta_cookies.txt",
    "tiktok.com": "tiktok_cookies.txt",
    "youtube.com": "youtube_cookies.txt",
    "youtu.be": "youtube_cookies.txt",
    "facebook.com": "facebook_cookies.txt",
    "fb.watch": "facebook_cookies.txt",
    "twitter.com": "twitter_cookies.txt",
    "x.com": "twitter_cookies.txt",
    "pinterest.com": "pinterest_cookies.txt",
    "pin.it": "pinterest_cookies.txt",
}

_warned_missing_cookies = set()


def get_cookie_file(url: str):
    host = (urlparse(url).hostname or "").lower()
    for domain, fname in COOKIE_MAP.items():
        if host == domain or host.endswith("." + domain):
            path = os.path.join(COOKIE_DIR, fname)
            if os.path.exists(path):
                return path
            if fname not in _warned_missing_cookies:
                log.warning(
                    f"No cookie file found at '{path}' for domain '{domain}'."
                )
                _warned_missing_cookies.add(fname)
            return None
    return None


def build_ydl_opts(url: str, quality: str = "best"):
    cookie_file = get_cookie_file(url)
    is_tiktok = "tiktok.com" in url.lower()

    # TikTok-এর ওয়াটারমার্ক ছাড়া MP4 সিলেকশন
    if is_tiktok:
        fmt = "bestvideo[vcodec!=h265]+bestaudio/best"
    else:
        fmt = "best[ext=mp4]/best"

    opts = {
        'format': fmt,
        'noplaylist': True,
        'extract_flat': False,
        'quiet': True,
        'no_warnings': True,
        'geo_bypass': True,
        'nocheckcertificate': True,
        'socket_timeout': 15,
        'retries': 5,
        'force_ipv4': True,
        'cachedir': False,
        'extractor_args': {
            'youtube': {
                'player_client': ['tv', 'web_safari', 'ios', 'web'],
            },
            'tiktok': {
                'api_hostname': 'api16-normal-c-useast1a.tiktokv.com',
                'app_info': '7355728856979392262',
            },
        },
        'http_headers': {
            'User-Agent': ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                           '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'),
            'Accept-Language': 'en-US,en;q=0.9',
            'Sec-Fetch-Mode': 'navigate',
        }
    }

    if cookie_file:
        opts['cookiefile'] = cookie_file
        log.info(f"Using cookie file: {cookie_file}")

    return opts


def pick_best_link(info: dict):
    formats = info.get("formats", []) or []

    # 1. MP4 Formats with Video & Audio
    mp4_formats = [
        f for f in formats
        if f.get("url")
        and f.get("ext") == "mp4"
        and "m3u8" not in (f.get("protocol") or "")
        and f.get("vcodec") not in (None, "none")
        and f.get("acodec") not in (None, "none")
    ]

    if mp4_formats:
        mp4_formats.sort(key=lambda f: ((f.get("height") or 0), (f.get("tbr") or 0)), reverse=True)
        return mp4_formats[0]["url"], mp4_formats[0].get("format_note", ""), "video"

    # 2. Check top-level URL
    direct_link = info.get("url")
    if direct_link:
        is_image = info.get("vcodec") in (None, "none") and info.get("ext") in ("jpg", "jpeg", "png", "webp")
        return direct_link, info.get("format_note", ""), "image" if is_image else "video"

    # 3. Fallback: Any non-m3u8 format with Video & Audio
    non_hls = [
        f for f in formats 
        if f.get("url") 
        and "m3u8" not in (f.get("protocol") or "")
        and f.get("vcodec") not in (None, "none")
        and f.get("acodec") not in (None, "none")
    ]
    if non_hls:
        non_hls.sort(key=lambda f: ((f.get("height") or 0), (f.get("tbr") or 0)), reverse=True)
        return non_hls[0]["url"], non_hls[0].get("format_note", ""), "video"

    # 4. TikTok Specific Fallback
    if formats:
        tiktok_wf = [f for f in formats if f.get("url") and "watermark" not in str(f.get("format_note", "")).lower()]
        if tiktok_wf:
            tiktok_wf.sort(key=lambda f: ((f.get("height") or 0), (f.get("tbr") or 0)), reverse=True)
            return tiktok_wf[0]["url"], tiktok_wf[0].get("format_note", ""), "video"

        formats_sorted = sorted(formats, key=lambda f: ((f.get("height") or 0), (f.get("tbr") or 0)), reverse=True)
        best = formats_sorted[0]
        if best.get("url"):
            return best["url"], (best.get("format_note", "") or "") + " (hls)", "video"

    return None, None, None


def extract_info_blocking(url: str, opts: dict):
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=False)


def extract_with_retry(url: str, opts: dict, attempts: int = 3, delay: float = 1.0):
    last_err = None
    non_retryable_markers = ("private", "sign in", "login", "unavailable", "removed")

    for attempt in range(1, attempts + 1):
        try:
            return extract_info_blocking(url, opts)
        except yt_dlp.utils.DownloadError as e:
            last_err = e
            msg = str(e).lower()
            if any(m in msg for m in non_retryable_markers) or attempt == attempts:
                raise
            log.warning(f"Attempt {attempt} failed for {url}, retrying: {e}")
            time.sleep(delay)
    raise last_err


def require_api_key():
    if not API_KEY:
        return True
    provided = request.headers.get("X-API-Key") or request.args.get("api_key")
    return provided == API_KEY


@app.route('/api/download', methods=['GET', 'POST'])
@app.route('/alldl', methods=['GET', 'POST'])
@limiter.limit(os.environ.get("RATE_LIMIT", "30 per minute"))
def download_video():
    if not require_api_key():
        return jsonify({"status": "error", "message": "Invalid or missing API key."}), 401

    video_url = request.values.get('url', '').strip()
    quality = request.values.get('quality', '').strip()
    no_cache = request.values.get('no_cache', '').lower() == 'true'

    if not video_url:
        return jsonify({"status": "error", "message": "URL is missing."}), 400

    if not is_allowed_url(video_url):
        return jsonify({
            "status": "error",
            "message": "Unsupported or disallowed URL/domain."
        }), 400

    cache_key = f"{video_url}::{quality or 'best'}"
    if not no_cache:
        cached = cache_get(cache_key)
        if cached:
            log.info(f"Cache hit: {video_url}")
            return jsonify(cached)

    ydl_opts = build_ydl_opts(video_url, quality)

    try:
        future = executor.submit(extract_with_retry, video_url, ydl_opts)
        try:
            info = future.result(timeout=EXTRACT_TIMEOUT)
        except FutureTimeoutError:
            log.warning(f"Extraction timed out: {video_url}")
            return jsonify({
                "status": "error",
                "message": f"Extraction timed out after {EXTRACT_TIMEOUT}s. Try again."
            }), 504

        direct_link, format_note, media_type = pick_best_link(info)

        if not direct_link:
            return jsonify({"status": "error", "message": "Could not extract download link."}), 404

        title = info.get('title', 'No Title')
        uploader = info.get('uploader', 'Unknown')
        duration = info.get('duration')
        thumbnail = info.get('thumbnail')
        icon = "🎥" if media_type == "video" else "🖼️"

        payload = {
            "status": "success",
            "result": direct_link,
            "cp": f"{icon} {title}\n👤 Uploader: {uploader}",
            "title": title,
            "uploader": uploader,
            "duration": duration,
            "thumbnail": thumbnail,
            "media_type": media_type,
            "format_note": format_note or None,
            "source": urlparse(video_url).hostname,
        }

        cache_set(cache_key, payload)
        return jsonify(payload)

    except yt_dlp.utils.DownloadError as e:
        msg = str(e)
        log.warning(f"DownloadError for {video_url}: {msg}")

        lower_msg = msg.lower()
        if "private video" in lower_msg:
            friendly = "This video is private."
        elif "this video is unavailable" in lower_msg or "video unavailable" in lower_msg:
            friendly = "Video unavailable (removed, region-locked, or deleted)."
        elif "sign in" in lower_msg or "login" in lower_msg or "log in" in lower_msg:
            friendly = "This content requires login — cookie file may be missing or expired."
        elif "unable to extract" in lower_msg or "unsupported url" in lower_msg:
            friendly = "The site's page structure changed and yt-dlp couldn't parse it. Try updating yt-dlp."
        elif "http error 403" in lower_msg or "forbidden" in lower_msg:
            friendly = "Access blocked by the platform (403). Cookies may be required or expired."
        elif "429" in lower_msg or "too many requests" in lower_msg:
            friendly = "Rate limited by the platform. Try again later."
        else:
            friendly = "Download extraction failed."

        return jsonify({"status": "error", "message": friendly, "details": msg}), 500

    except Exception as e:
        log.exception("Unexpected error")
        return jsonify({"status": "error", "message": "Internal server error.", "details": str(e)}), 500


@app.route('/api/cache/clear', methods=['POST'])
def clear_cache():
    if not require_api_key():
        return jsonify({"status": "error", "message": "Invalid or missing API key."}), 401
    with _cache_lock:
        _cache.clear()
    return jsonify({"status": "success", "message": "Cache cleared."})


@app.route('/', methods=['GET'])
def home():
    return jsonify({
        "status": "online",
        "message": "Production API is running.",
        "yt_dlp_version": yt_dlp.version.__version__,
        "cache_entries": len(_cache),
        "allowed_domains": ALLOWED_DOMAINS,
    })


@app.errorhandler(429)
def ratelimit_handler(e):
    return jsonify({"status": "error", "message": "Too many requests, slow down."}), 429


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("DEBUG", "false").lower() == "true"
    app.run(host="0.0.0.0", port=port, debug=debug, threaded=True)
