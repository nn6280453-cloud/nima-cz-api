# ========================================================
# Premium Sinhalasub.lk API
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
BASE_URL = "https://sinhalasub.lk"

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
# HOME
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
            "resolve": "/api/resolve?url=sinhalasub_links_url&api_key=YOUR_KEY",
            "debug": "/api/debug?url=movie_page_url",
            "stats": "/api/stats?api_key=YOUR_KEY"
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

        selectors = [
            "article.item",
            ".module-item",
            ".owl-item",
            "article",
            ".result-item",
            ".item-box",
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
        return jsonify({"success": False, "error": str(e)}), 500


# ========================================================
# 2. MOVIE — FIXED (uses .link-opt + .quality)
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

        if not title or title == "N/A":
            if soup.title and soup.title.string:
                title = soup.title.string.split("|")[0].strip()

        # Cleanup: only remove pipe-suffix if it contains "Sinhalasub"
        if title and ("sinhalasub" in title.lower() or "sinhala sub" in title.lower()):
            title = re.split(r"\s*\|\s*", title)[0].strip()

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
        # Structure: .link-opt > a[href="/links/xxx/"] (text = server name)
        # quality is in .quality sibling
        download_links = []
        seen = set()

        for lo in soup.select(".link-opt"):
            a_el = lo.find("a", href=True)
            if not a_el:
                continue
            href = a_el["href"].strip()
            server_name = a_el.get_text(" ", strip=True)

            if not href or href in seen:
                continue
            if href.startswith("#") or href.startswith("javascript:"):
                continue

            if not href.startswith("http"):
                href = urljoin(BASE_URL, href)

            seen.add(href)

            # Find quality inside the same .link-opt parent
            quality_el = lo.select_one(".quality")
            quality = quality_el.get_text(" ", strip=True) if quality_el else ""

            # If quality not found, try parent/sibling
            if not quality:
                parent = lo.parent
                if parent:
                    q2 = parent.select_one(".quality")
                    if q2:
                        quality = q2.get_text(" ", strip=True)

            # Clean quality
            quality = re.sub(r"\s+", " ", quality).strip()
            if not quality:
                # Try extracting from server_name
                qm = re.search(r"\b(\d{3,4}p|4K|2160p)\b", server_name, re.IGNORECASE)
                quality = qm.group(0).upper() if qm else "Download"

            # Size from server_name or quality text
            size = ""
            sm = re.search(r"(\d+(?:\.\d+)?\s*(?:MB|GB|KB))", server_name + " " + quality, re.IGNORECASE)
            if sm:
                size = sm.group(1).strip()

            download_links.append({
                "server": server_name or "Unknown",
                "quality": quality,
                "size": size,
                "label": f"{server_name} • {quality}".strip(" •"),
                "url": href
            })

        # Fallback if no .link-opt found
        if not download_links:
            for a in soup.find_all("a", href=True):
                href = a["href"]
                if "/links/" not in href:
                    continue
                if href in seen:
                    continue
                seen.add(href)
                if not href.startswith("http"):
                    href = urljoin(BASE_URL, href)
                download_links.append({
                    "server": a.get_text(strip=True) or "Download",
                    "quality": "Download",
                    "size": "",
                    "label": a.get_text(strip=True)[:80],
                    "url": href
                })

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
# 3. RESOLVE — sinhalasub.lk/links/xxx/ → real URL
# ========================================================
@app.route("/api/resolve")
@require_api_key
def resolve():
    src = request.args.get("url")
    if not src:
        return jsonify({"success": False, "error": 'Parameter "url" is required'}), 400

    cache_key = f"resolve:{src}"
    cached = cache_get(cache_key)
    if cached:
        cached["cached"] = True
        return jsonify(cached)

    try:
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
            final_url = src
            try:
                page.goto(src, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(5000)
                final_url = page.url
                html = page.content()
            finally:
                browser.close()

        # Try to find direct link in resolved HTML too
        soup = BeautifulSoup(html, "html.parser")
        direct = None
        for a in soup.find_all("a", href=True):
            h = a["href"]
            if any(x in h for x in ["pixeldrain.com", "drive.google.com", "mega.nz", ".mp4", ".mkv"]):
                direct = h
                break

        result = {
            "success": True,
            "cached": False,
            "data": {
                "source": src,
                "finalUrl": final_url,
                "directLink": direct or final_url
            }
        }
        cache_set(cache_key, result)
        return jsonify(result)

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ========================================================
# 4. DEBUG
# ========================================================
@app.route("/api/debug")
def debug():
    url = request.args.get("url")
    if not url:
        return jsonify({"error": 'Parameter "url" is required'}), 400

    try:
        html = fetch_with_browser(url)
        soup = BeautifulSoup(html, "html.parser")

        meta_title = ""
        og = soup.find("meta", property="og:title")
        if og and og.get("content"):
            meta_title = og["content"].strip()

        h1_title = ""
        h1_el = soup.select_one("h1.entry-title, h1")
        if h1_el:
            h1_title = h1_el.get_text(strip=True)

        # Sample of .link-opt structure
        link_opts = []
        for lo in soup.select(".link-opt")[:5]:
            a_el = lo.find("a", href=True)
            q_el = lo.select_one(".quality")
            link_opts.append({
                "outer_html": str(lo)[:500],
                "server_name": a_el.get_text(strip=True) if a_el else "",
                "href": (a_el["href"] if a_el else "")[:150],
                "quality_text": q_el.get_text(strip=True) if q_el else ""
            })

        return jsonify({
            "success": True,
            "url": url,
            "page_title_meta": meta_title,
            "page_title_h1": h1_title,
            "page_title_tag": soup.title.string if soup.title else "",
            "link_opt_count": len(soup.select(".link-opt")),
            "link_opts_sample": link_opts
        })

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ========================================================
# START
# ========================================================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 3000))
    app.run(host="0.0.0.0", port=port)
