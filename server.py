# ========================================================
# Nima Movie API - Baiscope.lk
# Author: Nima
# Deploy: Railway / Render
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
from urllib.parse import quote, urljoin

app = Flask(__name__)
CORS(app)

# ========================================================
# CONFIG
# ========================================================
BASE_URL = "https://www.baiscope.lk"

API_KEYS = {
    "nima_premium_88c183708c1971f39f2d2a25fbee7d8f": {
        "name": "Nima Premium",
        "rate_limit": 60,
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

cache_store = {}
rate_store = defaultdict(list)
daily_store = defaultdict(lambda: {"count": 0, "date": ""})
CACHE_TTL = 300
RATE_WINDOW = 60


# ========================================================
# HELPERS
# ========================================================
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
    now = time.time()
    rate_store[api_key] = [t for t in rate_store[api_key] if now - t < RATE_WINDOW]
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


def require_api_key(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        api_key = request.args.get("api_key") or request.headers.get("X-API-Key")
        if not api_key:
            return jsonify({"success": False, "error": "API key required"}), 401
        if api_key not in API_KEYS:
            return jsonify({"success": False, "error": "Invalid API key"}), 403
        info = API_KEYS[api_key]
        if not check_rate_limit(api_key, info["rate_limit"]):
            return jsonify({"success": False, "error": "Rate limit exceeded"}), 429
        if not check_daily_limit(api_key, info["daily_limit"]):
            return jsonify({"success": False, "error": "Daily limit exceeded"}), 429
        return f(*args, **kwargs)
    return wrapper


def fetch_with_browser(url, wait_ms=5000, scroll=True):
    """Fetch HTML using Playwright with auto-scroll for lazy-loaded content."""
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

            # Auto-scroll to trigger lazy loading
            if scroll:
                page.evaluate("""
                    async () => {
                        await new Promise((resolve) => {
                            let totalHeight = 0;
                            const distance = 400;
                            const timer = setInterval(() => {
                                const scrollHeight = document.body.scrollHeight;
                                window.scrollBy(0, distance);
                                totalHeight += distance;

                                if (totalHeight >= scrollHeight || totalHeight > 30000) {
                                    clearInterval(timer);
                                    resolve();
                                }
                            }, 250);
                        });
                    }
                """)
                page.wait_for_timeout(3000)
                page.evaluate("window.scrollTo(0, 0)")
                page.wait_for_timeout(1000)

            html = page.content()
        finally:
            browser.close()
        return html


def parse_size_mb(size_str):
    if not size_str:
        return 0
    m = re.search(r"(\d+(?:\.\d+)?)\s*(GB|MB|KB)", size_str, re.IGNORECASE)
    if not m:
        return 0
    num = float(m.group(1))
    unit = m.group(2).upper()
    if unit == "GB":
        return int(num * 1024)
    if unit == "MB":
        return int(num)
    if unit == "KB":
        return int(num / 1024)
    return 0


def extract_drive_id(href):
    if not href:
        return None
    m1 = re.search(r"/file/d/([a-zA-Z0-9_-]+)", href)
    m2 = re.search(r"[?&]id=([a-zA-Z0-9_-]+)", href)
    m3 = re.search(r"/d/([a-zA-Z0-9_-]+)", href)
    if m1:
        return m1.group(1)
    if m2:
        return m2.group(1)
    if m3:
        return m3.group(1)
    return None


# ========================================================
# HOME
# ========================================================
@app.route("/")
def home():
    return jsonify({
        "status": "ok",
        "name": "Nima Movie API",
        "version": "2.2.0",
        "source": BASE_URL,
        "endpoints": {
            "search": "/api/search?q=movie+name&api_key=YOUR_KEY",
            "movie": "/api/movie?url=movie_page_url&api_key=YOUR_KEY",
            "stats": "/api/stats?api_key=YOUR_KEY",
            "debug": "/api/debug?url=any_page_url",
            "debug_search": "/api/debug/search?q=movie+name"
        }
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
            "cache_size": len(cache_store)
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

    cache_key = f"search:{query.lower()}"
    cached = cache_get(cache_key)
    if cached:
        cached["cached"] = True
        return jsonify(cached)

    try:
        search_url = f"{BASE_URL}/?s={quote(query)}"
        html = fetch_with_browser(search_url, scroll=False)
        soup = BeautifulSoup(html, "html.parser")

        results = []
        seen_urls = set()

        # Baiscope uses .post-card
        for item in soup.select(".post-card, article, .result-item, .item-box"):
            link_el = item.find("a", href=True)
            if not link_el:
                continue
            link = link_el["href"]
            if not link.startswith("http"):
                link = urljoin(BASE_URL, link)
            if link in seen_urls or "baiscope" not in link:
                continue

            if any(x in link for x in ["/category/", "/tag/", "/page/", "/author/", "#"]):
                continue

            title_el = (
                item.select_one("h3")
                or item.select_one("h2")
                or item.select_one(".entry-title a")
                or item.select_one(".title a")
                or link_el
            )
            title = title_el.get_text(strip=True) if title_el else ""

            if not title or len(title) < 3:
                continue

            img_el = item.find("img")
            poster = ""
            if img_el:
                poster = img_el.get("src") or img_el.get("data-src") or ""

            text = item.get_text(" ", strip=True)
            year_m = re.search(r"\b(19|20)\d{2}\b", text)
            year = year_m.group(0) if year_m else "N/A"

            seen_urls.add(link)
            results.append({
                "title": title,
                "year": year,
                "poster": poster,
                "url": link
            })

        response = {
            "success": True,
            "cached": False,
            "count": len(results),
            "data": results[:20]
        }
        cache_set(cache_key, response)
        return jsonify(response)

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


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
        html = fetch_with_browser(movie_url, scroll=True)
        soup = BeautifulSoup(html, "html.parser")

        # ---- TITLE ----
        title = ""
        og = soup.find("meta", property="og:title")
        if og and og.get("content"):
            title = og["content"].strip()

        if not title or title == "N/A":
            for sel in ["h1.entry-title", "h1.post-title", ".entry-header h1", "article h1", "h1"]:
                el = soup.select_one(sel)
                if el:
                    t = el.get_text(strip=True)
                    if t:
                        title = t
                        break

        if not title and soup.title and soup.title.string:
            title = soup.title.string.split("|")[0].strip()

        if not title:
            title = "N/A"

        # ---- POSTER ----
        poster = ""
        ogi = soup.find("meta", property="og:image")
        if ogi and ogi.get("content"):
            poster = ogi["content"].strip()
        else:
            p_el = soup.select_one(".poster img, article img, .entry-content img")
            if p_el:
                poster = p_el.get("src") or p_el.get("data-src") or ""

        # ---- DESCRIPTION ----
        desc = ""
        ogd = soup.find("meta", property="og:description")
        if ogd and ogd.get("content"):
            desc = ogd["content"].strip()[:500]

        # ---- YEAR ----
        year_m = re.search(r"\b(19|20)\d{2}\b", movie_url) or re.search(r"\b(19|20)\d{2}\b", title)
        year = year_m.group(0) if year_m else "N/A"

        # ---- RATING ----
        rating = "N/A"
        full = soup.get_text(" ", strip=True)
        m = re.search(r"IMDb[:\s]*([\d]+\.[\d]+)", full)
        if m:
            rating = m.group(1)

        # ---- DOWNLOAD LINKS ----
        # Collect Google Drive AND other hosts
        download_links = []
        seen = set()

        host_patterns = [
            ("Google Drive", re.compile(r"drive\.google\.com|drive\.usercontent\.google\.com")),
            ("Mega", re.compile(r"mega\.nz")),
            ("PixelDrain", re.compile(r"pixeldrain\.com")),
            ("MediaFire", re.compile(r"mediafire\.com")),
            ("Dropbox", re.compile(r"dropbox\.com")),
            ("1Fichier", re.compile(r"1fichier\.com")),
            ("Direct MP4", re.compile(r"\.mp4(\?|$)")),
            ("Direct MKV", re.compile(r"\.mkv(\?|$)")),
        ]

        for a in soup.find_all("a", href=True):
            href = a["href"]
            label = a.get_text(" ", strip=True)

            host_name = None
            for name, pattern in host_patterns:
                if pattern.search(href):
                    host_name = name
                    break

            if not host_name:
                continue

            if href in seen:
                continue
            seen.add(href)

            fid = extract_drive_id(href)

            qm = re.search(r"\b(\d{3,4}p|4K|2160p)\b", label, re.IGNORECASE)
            quality = qm.group(0).upper() if qm else "Download"

            size_m = re.search(r"(\d+(?:\.\d+)?\s*(?:MB|GB|KB))", label, re.IGNORECASE)
            size = size_m.group(1).strip() if size_m else ""

            size_mb = parse_size_mb(size)
            is_hevc = any(x in label.lower() for x in ["hevc", "x265", "h265"])

            entry = {
                "host": host_name,
                "quality": quality,
                "size": size,
                "sizeMB": size_mb,
                "isHEVC": is_hevc,
                "codec": "HEVC" if is_hevc else "H.264",
                "label": label[:150],
                "url": href
            }

            if fid:
                entry["directUrl"] = (
                    f"https://drive.usercontent.google.com/download"
                    f"?id={fid}&export=download&confirm=t"
                )
            else:
                entry["directUrl"] = href

            download_links.append(entry)

        # Sort — smallest first
        download_links.sort(key=lambda x: x["sizeMB"] or 999999)

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
# 3. DEBUG
# ========================================================
@app.route("/api/debug")
def debug():
    url = request.args.get("url")
    if not url:
        return jsonify({"error": 'Parameter "url" is required'}), 400

    try:
        html = fetch_with_browser(url, scroll=True)
        soup = BeautifulSoup(html, "html.parser")

        meta_title = ""
        og = soup.find("meta", property="og:title")
        if og and og.get("content"):
            meta_title = og["content"].strip()

        h1_title = ""
        h1_el = soup.select_one("h1.entry-title, h1")
        if h1_el:
            h1_title = h1_el.get_text(strip=True)

        # All download-like links
        all_links = []
        for a in soup.find_all("a", href=True):
            h = a["href"]
            text = a.get_text(" ", strip=True)
            if not text and not h:
                continue
            if any(x in h for x in [
                "drive.google", "mega.nz", "pixeldrain", "mediafire",
                "dropbox", "1fichier", "/download", "/links/", ".mp4", ".mkv"
            ]):
                all_links.append({
                    "text": text[:120],
                    "href": h[:250],
                    "parent_class": " ".join(a.parent.get("class", [])) if a.parent else ""
                })

        class_counter = {}
        for tag in soup.find_all(class_=True):
            for c in tag.get("class", []):
                class_counter[c] = class_counter.get(c, 0) + 1
        top_classes = sorted(class_counter.items(), key=lambda x: -x[1])[:30]

        return jsonify({
            "success": True,
            "url": url,
            "content_length": len(html),
            "page_title_meta": meta_title,
            "page_title_h1": h1_title,
            "page_title_tag": soup.title.string if soup.title else "",
            "download_links_count": len(all_links),
            "download_links": all_links[:20],
            "top_classes": top_classes
        })

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ========================================================
# 4. DEBUG SEARCH
# ========================================================
@app.route("/api/debug/search")
def debug_search():
    query = request.args.get("q", "avatar")

    try:
        search_url = f"{BASE_URL}/?s={quote(query)}"
        html = fetch_with_browser(search_url, scroll=False)
        soup = BeautifulSoup(html, "html.parser")

        class_counter = {}
        for tag in soup.find_all(class_=True):
            for c in tag.get("class", []):
                class_counter[c] = class_counter.get(c, 0) + 1
        top_classes = sorted(class_counter.items(), key=lambda x: -x[1])[:30]

        all_links = []
        for a in soup.find_all("a", href=True):
            href = a["href"]
            text = a.get_text(strip=True)
            if not text or len(text) < 3:
                continue
            if any(x in href for x in ["/category/", "/tag/", "/page/", "#"]):
                continue
            all_links.append({
                "text": text[:80],
                "href": href[:150],
                "parent_class": " ".join(a.parent.get("class", [])) if a.parent else ""
            })

        return jsonify({
            "success": True,
            "query": query,
            "search_url": search_url,
            "content_length": len(html),
            "top_classes": top_classes,
            "sample_links": all_links[:25]
        })

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ========================================================
# START
# ========================================================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 3000))
    app.run(host="0.0.0.0", port=port)
