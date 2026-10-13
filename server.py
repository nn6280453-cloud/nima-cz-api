# ========================================================
# Premium Sinhalasub.lk API
# Author: Nima
# Deploy: Railway (Docker)
# ========================================================

from flask import Flask, request, jsonify
from flask_cors import CORS
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright
from functools import wraps
from collections import defaultdict
import re
import os
import time
import hashlib
from urllib.parse import quote, urljoin

app = Flask(__name__)
CORS(app)

# ========================================================
# CONFIG
# ========================================================
BASE_URL = "https://sinhalasub.lk"

# Premium API Keys (ඔබට කැමති තරම් keys add කරන්න)
API_KEYS = {
    "nima_premium_88c183708c1971f39f2d2a25fbee7d8f": {
        "name": "Nima Premium",
        "rate_limit": 60,      # requests per minute
        "daily_limit": 5000,
        "tier": "premium"
    },
    "chama_api_88c183708c1971f39f2d2a25fbee7d8f": {
        "name": "Chama API",
        "rate_limit": 30,
        "daily_limit": 2000,
        "tier": "standard"
    }
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

# ========================================================
# CACHE + RATE LIMIT STORAGE
# ========================================================
cache_store = {}                       # key -> { data, at }
rate_store = defaultdict(list)         # api_key -> [timestamps]
daily_store = defaultdict(lambda: {"count": 0, "date": ""})

CACHE_TTL = 300                        # 5 minutes
RATE_WINDOW = 60                       # 1 minute


def cache_get(key):
    entry = cache_store.get(key)
    if not entry:
        return None
    if time.time() - entry["at"] > CACHE_TTL:
        del cache_store[key]
        return None
    return entry["data"]


def cache_set(key, data):
    cache_store[key] = {"data": data, "at": time.time()}


def check_rate_limit(api_key, limit):
    """True if allowed, False if exceeded."""
    now = time.time()
    timestamps = rate_store[api_key]
    # Remove old entries
    rate_store[api_key] = [t for t in timestamps if now - t < RATE_WINDOW]
    if len(rate_store[api_key]) >= limit:
        return False
    rate_store[api_key].append(now)
    return True


def check_daily_limit(api_key, limit):
    today = time.strftime("%Y-%m-%d")
    entry = daily_store[api_key]
    if entry["date"] != today:
        entry["count"] = 0
        entry["date"] = today
    if entry["count"] >= limit:
        return False
    entry["count"] += 1
    return True


# ========================================================
# AUTH DECORATOR
# ========================================================
def require_api_key(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        api_key = request.args.get("api_key") or request.headers.get("X-API-Key")

        if not api_key:
            return jsonify({
                "success": False,
                "error": "API key required",
                "hint": "Add ?api_key=YOUR_KEY to the request"
            }), 401

        if api_key not in API_KEYS:
            return jsonify({
                "success": False,
                "error": "Invalid API key"
            }), 403

        key_info = API_KEYS[api_key]

        # Rate limit check
        if not check_rate_limit(api_key, key_info["rate_limit"]):
            return jsonify({
                "success": False,
                "error": "Rate limit exceeded",
                "limit": f"{key_info['rate_limit']} requests/minute"
            }), 429

        # Daily limit check
        if not check_daily_limit(api_key, key_info["daily_limit"]):
            return jsonify({
                "success": False,
                "error": "Daily limit exceeded",
                "limit": key_info["daily_limit"]
            }), 429

        return f(*args, **kwargs)
    return wrapper


# ========================================================
# BROWSER FETCH
# ========================================================
def fetch_with_browser(url, wait_ms=4000):
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--single-process",
                "--no-zygote",
            ]
        )
        page = browser.new_page(user_agent=HEADERS["User-Agent"])
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(wait_ms)
            html = page.content()
        finally:
            browser.close()
        return html


# ========================================================
# HEALTH / ROOT
# ========================================================
@app.route("/")
def home():
    return jsonify({
        "status": "ok",
        "name": "Sinhalasub Premium API",
        "version": "1.0.0",
        "base_url": BASE_URL,
        "endpoints": {
            "search": "/api/search?q=movie+name&api_key=YOUR_KEY",
            "movie": "/api/movie?url=movie_page_url&api_key=YOUR_KEY",
            "stats": "/api/stats?api_key=YOUR_KEY"
        },
        "note": "All endpoints require valid API key"
    })


# ========================================================
# STATS
# ========================================================
@app.route("/api/stats")
@require_api_key
def stats():
    api_key = request.args.get("api_key")
    today = time.strftime("%Y-%m-%d")
    entry = daily_store[api_key]
    return jsonify({
        "success": True,
        "data": {
            "key_name": API_KEYS[api_key]["name"],
            "tier": API_KEYS[api_key]["tier"],
            "rate_limit": f"{API_KEYS[api_key]['rate_limit']} per minute",
            "daily_limit": API_KEYS[api_key]["daily_limit"],
            "daily_used": entry["count"] if entry["date"] == today else 0,
            "cache_size": len(cache_store),
            "active_keys": len(API_KEYS)
        }
    })


# ========================================================
# 1. SEARCH
# ========================================================
@app.route("/api/search")
@require_api_key
def search():
    query = request.args.get("q")
    if not query:
        return jsonify({"success": False, "error": 'Parameter "q" is required'}), 400

    # Cache check
    cache_key = f"search:{query.lower()}"
    cached = cache_get(cache_key)
    if cached:
        cached["cached"] = True
        return jsonify(cached)

    try:
        search_url = f"{BASE_URL}/?s={quote(query)}"
        html = fetch_with_browser(search_url)
        soup = BeautifulSoup(html, "html.parser")

        results = []
        seen_urls = set()

        # Try multiple selector patterns
        selectors = [
            "article.item",
            "article",
            ".result-item",
            ".movie-item",
            ".item-box",
            ".post-item",
            ".entry-item",
        ]

        for selector in selectors:
            items = soup.select(selector)
            if items:
                for item in items:
                    link_el = item.find("a", href=True)
                    if not link_el:
                        continue
                    link = link_el["href"]
                    if not link.startswith("http"):
                        link = urljoin(BASE_URL, link)
                    if "sinhalasub" not in link or link in seen_urls:
                        continue

                    title_el = (
                        item.select_one(".title a")
                        or item.select_one("h2 a")
                        or item.select_one("h3 a")
                        or item.select_one(".entry-title a")
                        or item.select_one(".item-desc-title")
                        or link_el
                    )
                    title = title_el.get_text(strip=True) if title_el else ""

                    img_el = item.find("img")
                    poster = ""
                    if img_el:
                        poster = img_el.get("src") or img_el.get("data-src") or ""

                    text = item.get_text(" ", strip=True)
                    year_m = re.search(r"\b(19|20)\d{2}\b", text)
                    year = year_m.group(0) if year_m else "N/A"

                    rating_el = item.select_one(".rating, .imdb-score, .vote")
                    rating = rating_el.get_text(strip=True) if rating_el else "N/A"
                    rating = re.sub(r"[^\d.]", "", rating) or "N/A"

                    quality_el = item.select_one(".quality, .badge-quality-corner")
                    quality = quality_el.get_text(strip=True) if quality_el else ""

                    if title and link:
                        seen_urls.add(link)
                        results.append({
                            "title": title,
                            "year": year,
                            "rating": rating,
                            "quality": quality,
                            "poster": poster,
                            "url": link
                        })
                if results:
                    break

        response = {
            "success": True,
            "cached": False,
            "count": len(results),
            "data": results[:20]
        }
        cache_set(cache_key, response)

        return jsonify(response)

    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e),
            "hint": "Playwright browser error"
        }), 500


# ========================================================
# 2. MOVIE DETAILS
# ========================================================
@app.route("/api/movie")
@require_api_key
def movie():
    movie_url = request.args.get("url")
    if not movie_url:
        return jsonify({"success": False, "error": 'Parameter "url" is required'}), 400

    cache_key = f"movie:{movie_url}"
    cached = cache_get(cache_key)
    if cached:
        cached["cached"] = True
        return jsonify(cached)

    try:
        html = fetch_with_browser(movie_url)
        soup = BeautifulSoup(html, "html.parser")

        # Title
        title = ""
        og = soup.find("meta", property="og:title")
        if og and og.get("content"):
            title = og["content"].strip()
        if not title:
            t_el = (
                soup.select_one("h1.entry-title")
                or soup.select_one(".entry-header h1")
                or soup.select_one("h1")
            )
            title = t_el.get_text(strip=True) if t_el else "N/A"
        title = re.sub(r"\s*[-–|]\s*Sinhalasub.*$", "", title, flags=re.IGNORECASE).strip()
        title = re.sub(r"\s*[-–|]\s*Sinhala Sub.*$", "", title, flags=re.IGNORECASE).strip()

        # Poster
        poster = ""
        ogi = soup.find("meta", property="og:image")
        if ogi and ogi.get("content"):
            poster = ogi["content"].strip()
        else:
            p_el = soup.select_one(".poster img, article img, .entry-content img")
            if p_el:
                poster = p_el.get("src") or p_el.get("data-src") or ""

        # Description
        desc = ""
        ogd = soup.find("meta", property="og:description")
        if ogd and ogd.get("content"):
            desc = ogd["content"].strip()[:500]
        else:
            d_el = soup.select_one(".entry-content p, .description")
            if d_el:
                desc = d_el.get_text(strip=True)[:500]

        # Year
        year_m = re.search(r"\b(19|20)\d{2}\b", movie_url) or re.search(r"\b(19|20)\d{2}\b", title)
        year = year_m.group(0) if year_m else "N/A"

        # Rating
        rating = "N/A"
        r_el = soup.select_one(".imdb-score, .imdb-rating-badge, .rating")
        if r_el:
            m = re.search(r"(\d+\.\d+)", r_el.get_text(strip=True))
            if m:
                rating = m.group(1)
        if rating == "N/A":
            full = soup.get_text(" ", strip=True)
            m = re.search(r"IMDb[:\s]*([\d]+\.[\d]+)", full)
            if m:
                rating = m.group(1)

        # Download links
        # Sinhalasub typically uses <a href="...drive.google.com/..."> or similar
        download_links = []
        seen = set()

        link_pattern = re.compile(
            r"(drive\.google|mega\.nz|pixeldrain|hubcloud|workers\.dev|"
            r"gdflix|katdrive|kolop|1fichier|mediafire|dropbox|"
            r"sinhalasub\.lk/dl|sinhalasub\.net/dl|\.mp4|\.mkv)",
            re.IGNORECASE
        )
        quality_re = re.compile(r"\b(\d{3,4}p|4K|2160p|720p|1080p|480p)\b", re.IGNORECASE)
        size_re = re.compile(r"(\d+(?:\.\d+)?\s*(?:MB|GB|KB))", re.IGNORECASE)

        # Look for buttons with download classes
        candidate_anchors = (
            soup.select("a.button, a.dl-btn, a.download, .dl_links a, .download-links a")
            or soup.find_all("a", href=True)
        )

        for a in candidate_anchors:
            href = a.get("href", "").strip()
            label = a.get_text(" ", strip=True)

            if not href or href in seen:
                continue
            if href.startswith("#") or href.startswith("javascript:"):
                continue

            if not link_pattern.search(href) and not link_pattern.search(label):
                continue

            seen.add(href)

            q_match = quality_re.search(label) or quality_re.search(href)
            quality = q_match.group(0).upper() if q_match else "Download"

            s_match = size_re.search(label)
            size = s_match.group(1).strip() if s_match else ""

            download_links.append({
                "quality": quality,
                "size": size,
                "label": label[:120] or quality,
                "url": href
            })

        order = {"4K": 0, "2160P": 1, "1440P": 2, "1080P": 3, "720P": 4, "480P": 5, "360P": 6}
        download_links.sort(key=lambda x: order.get(x["quality"], 99))

        response = {
            "success": True,
            "cached": False,
            "data": {
                "title": title,
                "year": year,
                "rating": rating,
                "poster": poster,
                "description": desc,
                "totalLinks": len(download_links),
                "downloadLinks": download_links
            }
        }
        cache_set(cache_key, response)

        return jsonify(response)

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ========================================================
# START
# ========================================================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 3000))
    app.run(host="0.0.0.0", port=port)
