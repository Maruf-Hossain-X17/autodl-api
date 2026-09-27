# 🚀 Universal Video Downloader API

A fast, lightweight, and high-performance RESTful API built with **Flask** and **yt-dlp** to extract direct media download links from various social media platforms.

---

## ✨ Features

- **Multi-Platform Support:** Download videos/images from YouTube, TikTok, Instagram, Facebook, Twitter/X, and Pinterest.
- **TikTok Watermark-Free:** Smart extraction to fetch TikTok videos without watermarks.
- **In-Memory TTL Cache:** Fast response times for repeated requests without overloading target servers.
- **Multi-Threaded Extraction:** Powered by `ThreadPoolExecutor` with configurable timeout safeguards.
- **Rate Limiting:** Protects your server against spam using `Flask-Limiter`.
- **API Key Security:** Optional header/query parameter authentication for restricted usage.
- **Cookie File Integration:** Supports custom cookie files for restricted or age-gated content.

---

## 📋 Supported Platforms

| Platform | Domains Supported |
| :--- | :--- |
| **YouTube** | `youtube.com`, `youtu.be`, `m.youtube.com` |
| **Instagram** | `instagram.com`, `www.instagram.com` |
| **TikTok** | `tiktok.com`, `vm.tiktok.com`, `vt.tiktok.com` |
| **Facebook** | `facebook.com`, `fb.watch`, `m.facebook.com` |
| **Twitter / X** | `twitter.com`, `x.com` |
| **Pinterest** | `pinterest.com`, `pin.it` |

---

## ⚙️ Environment Variables

Customize the server configuration using environment variables:

| Variable | Default Value | Description |
| :--- | :--- | :--- |
| `PORT` | `5000` | Server port |
| `DEBUG` | `false` | Enable Flask debug mode (`true` / `false`) |
| `API_KEY` | *None* | Optional API key requirement (`X-API-Key` header or `api_key` param) |
| `ALLOWED_ORIGINS`| `*` | CORS allowed origins |
| `RATE_LIMIT` | `30 per minute` | Rate limit threshold per IP |
| `EXTRACT_TIMEOUT`| `30` | Max seconds to wait for link extraction |
| `MAX_WORKERS` | `16` | Thread pool size for handling extractions |
| `CACHE_TTL` | `300` | In-memory cache duration in seconds |
| `COOKIE_DIR` | `.` | Directory path containing cookie files |

---

## 🛠️ Installation & Setup

### 1. Requirements
- **Python 3.8+**
- **pip**

### 2. Install Dependencies
```bash
pip install flask flask-cors flask-limiter yt-dlp certifi
