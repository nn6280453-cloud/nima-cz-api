 # ========================================================
# Premium Movie API - Sinhalasub + Baiscope
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
from urllib.parse import quote, urljoin

app = Flask(__name__)
CORS(app)

# ========================================================
# CONFIG
# ========================================================
SINHALA_URL = "https://sinhalasub.lk"
BAISCOPE_URL = "https://baiscopedownloads.lk"

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
        "name": "Premium Movie API",
        "version": "1.2.0",
        "sources": {
            "sinhalasub": SINHALA_URL,
            "baiscope": BAISCOPE_URL
        },
        "endpoints": {
            "sinhalasub_search": "/api/search?q=movie+name&api_key=YOUR_KEY",
            "sinhalasub_movie": "/api/movie?url=movie_page_url&api_key=YOUR_KEY",
            "sinhalasub_resolve": "/api/resolve?url=sinhalasub_links_url&api_key=YOUR_KEY",
            "baiscope_search": "/api/baiscope/search?q=movie+name&api_key=YOUR_KEY",
            "baiscope_movie": "/api/baiscope/movie?url=movie_page_url&api_key=YOUR_KEY",
            "stats": "/api/stats?api_key=YOUR_KEY",
            "debug": "/api/debug?url=movie_page_url"
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
# 1. SINHALASUB SEARCH
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
        search_url = f"{SINHALA_URL}/?s={quote(query)}"
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
                        link = urljoin(SINHALA_URL, link)
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
# 2. SINHALASUB MOVIE DETAILS
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

        # TITLE
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

        if title and ("sinhalasub" in title.lower() or "sinhala sub" in title.lower()):
            title = re.split(r"\s*\|\s*", title)[0].strip()

        if not title:
            title = "N/A"

        # POSTER
        poster = ""
        ogi = soup.find("meta", property="og:image")
        if ogi and ogi.get("content"):
            poster = ogi["content"].strip()
        else:
            p_el = soup.select_one(".poster img, article img, .entry-content img")
            if p_el:
                poster = p_el.get("src") or p_el.get("data-src") or ""

        # DESCRIPTION
        desc = ""
        ogd = soup.find("meta", property="og:description")
        if ogd and ogd.get("content"):
            desc = ogd["content"].strip()[:500]

        # YEAR
        year_m = re.search(r"\b(19|20)\d{2}\b", movie_url) or re.search(r"\b(19|20)\d{2}\b", title)
        year = year_m.group(0) if year_m else "N/A"

        # RATING
        rating = "N/A"
        full = soup.get_text(" ", strip=True)
        m = re.search(r"IMDb[:\s]*([\d]+\.[\d]+)", full)
        if m:
            rating = m.group(1)

        # DOWNLOAD LINKS
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
                href = urljoin(SINHALA_URL, href)

            seen.add(href)

            quality_el = lo.select_one(".quality")
            quality = quality_el.get_text(" ", strip=True) if quality_el else ""

            if not quality:
                parent = lo.parent
                if parent:
                    q2 = parent.select_one(".quality")
                    if q2:
                        quality = q2.get_text(" ", strip=True)

            quality = re.sub(r"\s+", " ", quality).strip()
            if not quality:
                qm = re.search(r"\b(\d{3,4}p|4K|2160p)\b", server_name, re.IGNORECASE)
                quality = qm.group(0).upper() if qm else "Download"

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

        if not download_links:
            for a in soup.find_all("a", href=True):
                href = a["href"]
                if "/links/" not in href:
                    continue
                if href in seen:
                    continue
                seen.add(href)
                if not href.startswith("http"):
                    href = urljoin(SINHALA_URL, href)
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
# 3. SINHALASUB RESOLVE
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
            html = ""

            try:
                page.goto(src, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(8000)
                final_url = page.url
                html = page.content()

                soup = BeautifulSoup(html, "html.parser")
                meta_refresh = soup.find("meta", attrs={"http-equiv": re.compile("refresh", re.I)})
                if meta_refresh and meta_refresh.get("content"):
                    m = re.search(r"url=([^\s;]+)", meta_refresh["content"], re.IGNORECASE)
                    if m:
                        final_url = m.group(1).strip("\"'")

                js_redirect = re.search(
                    r'(?:window\.location|location\.href|location\.replace)\s*[=(]\s*["\']([^"\']+)["\']',
                    html
                )
                if js_redirect:
                    candidate = js_redirect.group(1)
                    if candidate.startswith("http") and "sinhalasub" not in candidate:
                        final_url = candidate

                try:
                    button_selectors = [
                        "a.btn-primary",
                        "a.btn-success",
                        "a#download",
                        "a.download-btn",
                        "button.btn",
                        ".link-opt a",
                        "a[href*='pixeldrain']",
                        "a[href*='drive.google']",
                        "a[href*='mega.nz']",
                    ]
                    for sel in button_selectors:
                        try:
                            btn = page.query_selector(sel)
                            if btn:
                                with page.context.expect_page(timeout=5000) as new_page_info:
                                    btn.click()
                                new_page = new_page_info.value
                                new_page.wait_for_load_state("domcontentloaded", timeout=15000)
                                final_url = new_page.url
                                html = new_page.content()
                                break
                        except Exception:
                            continue
                except Exception:
                    pass

                if "sinhalasub" in final_url:
                    page.wait_for_timeout(5000)
                    final_url = page.url
                    html = page.content()

            finally:
                browser.close()

        soup = BeautifulSoup(html, "html.parser")
        direct_hosts = re.compile(
            r"(pixeldrain\.com|drive\.google\.com|mega\.nz|mediafire\.com|dropbox\.com|1fichier\.com|"
            r"gdflix|hubcloud|workers\.dev|katdrive|kolop|files\.catbox|tmpsend|filepress)",
            re.IGNORECASE
        )

        direct = None

        if direct_hosts.search(final_url):
            direct = final_url
        else:
            for a in soup.find_all("a", href=True):
                h = a["href"]
                if direct_hosts.search(h):
                    direct = h
                    break

        if not direct and "sinhalasub" not in final_url:
            direct = final_url

        result = {
            "success": True,
            "cached": False,
            "data": {
                "source": src,
                "finalUrl": final_url,
                "directLink": direct or final_url,
                "hasDirect": bool(direct),
                "isResolved": direct is not None and "sinhalasub" not in (direct or ""),
                "pageTitle": soup.title.string.strip() if soup.title else ""
            }
        }
        cache_set(cache_key, result)
        return jsonify(result)

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ========================================================
# 4. BAISCOPE SEARCH
# ========================================================
@app.route("/api/baiscope/search")
@require_api_key
def baiscope_search():
    query = request.args.get("q")
    if not query:
        return jsonify({"success": False, "error": 'Parameter "q" is required'}), 400

    cache_key = f"bs-search:{query.lower()}"
    cached = cache_get(cache_key)
    if cached:
        cached["cached"] = True
        return jsonify(cached)

    try:
        search_url = f"{BAISCOPE_URL}/?s={quote(query)}"
        html = fetch_with_browser(search_url)
        soup = BeautifulSoup(html, "html.parser")

        results = []
        seen = set()

        selectors = ["article.item", "article", ".result-item", ".movie-item", ".item"]

        for selector in selectors:
            items = soup.select(selector)
            if items:
                for item in items:
                    link_el = item.find("a", href=True)
                    if not link_el:
                        continue
                    link = link_el["href"]
                    if not link.startswith("http"):
                        link = urljoin(BAISCOPE_URL, link)
                    if link in seen:
                        continue
                    if "baiscope" not in link:
                        continue

                    title_el = (
                        item.select_one(".entry-title a")
                        or item.select_one("h2 a")
                        or item.select_one("h3 a")
                        or link_el
                    )
                    title = title_el.get_text(strip=True) if title_el else ""

                    img_el = item.find("img")
                    poster = ""
                    if img_el:
                        poster = img_el.get("src") or img_el.get("data-src") or ""

                    text = item.get_text(" ", strip=True)
                    ym = re.search(r"\b(19|20)\d{2}\b", text)
                    year = ym.group(0) if ym else "N/A"

                    if title:
                        seen.add(link)
                        results.append({
                            "title": title,
                            "year": year,
                            "poster": poster,
                            "url": link
                        })
                if results:
                    break

        response = {"success": True, "cached": False, "count": len(results), "data": results[:20]}
        cache_set(cache_key, response)
        return jsonify(response)

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ========================================================
# 5. BAISCOPE MOVIE DETAILS (with Google Drive direct links)
# ========================================================
@app.route("/api/baiscope/movie")
@require_api_key
def baiscope_movie():
    movie_url = request.args.get("url")
    if not movie_url:
        return jsonify({"success": False, "error": 'Parameter "url" is required'}), 400

    cache_key = f"bs-movie:{movie_url}"
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
            t = soup.select_one("h1.entry-title, h1")
            title = t.get_text(strip=True) if t else "N/A"

        if title and "baiscope" in title.lower():
            title = re.split(r"\s*[-–|]\s*", title)[0].strip()

        # Poster
        poster = ""
        ogi = soup.find("meta", property="og:image")
        if ogi and ogi.get("content"):
            poster = ogi["content"].strip()
        else:
            p = soup.select_one(".poster img, article img")
            if p:
                poster = p.get("src") or p.get("data-src") or ""

        # Year
        ym = re.search(r"\b(19|20)\d{2}\b", movie_url) or re.search(r"\b(19|20)\d{2}\b", title)
        year = ym.group(0) if ym else "N/A"

        # Rating
        rating = "N/A"
        full = soup.get_text(" ", strip=True)
        rm = re.search(r"IMDb[:\s]*([\d]+\.[\d]+)", full)
        if rm:
            rating = rm.group(1)

        # Google Drive links (direct)
        download_links = []
        seen = set()

        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "drive.google.com" not in href and "drive.usercontent" not in href:
                continue
            if href in seen:
                continue
            seen.add(href)

            label = a.get_text(" ", strip=True)

            # Extract Google Drive file ID
            fid = None
            m1 = re.search(r"/file/d/([a-zA-Z0-9_-]+)", href)
            m2 = re.search(r"[?&]id=([a-zA-Z0-9_-]+)", href)
            if m1:
                fid = m1.group(1)
            elif m2:
                fid = m2.group(1)

            if not fid:
                continue

            # Quality detection
            qm = re.search(r"\b(\d{3,4}p|4K|2160p)\b", label, re.IGNORECASE)
            quality = qm.group(0).upper() if qm else "Download"

            # Size detection
            size_m = re.search(r"(\d+(?:\.\d+)?\s*(?:MB|GB|KB))", label, re.IGNORECASE)
            size = size_m.group(1).strip() if size_m else ""

            download_links.append({
                "quality": quality,
                "size": size,
                "label": label[:120],
                "driveId": fid,
                "url": href,
                "directUrl": f"https://drive.usercontent.google.com/download?id={fid}&export=download&confirm=t"
            })

        # Sort by quality
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
                "totalLinks": len(download_links),
                "downloadLinks": download_links
            }
        }
        cache_set(cache_key, response)
        return jsonify(response)

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ========================================================
# 6. DEBUG
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

        # Google Drive links
        gdrive_links = []
        for a in soup.find_all("a", href=True):
            h = a["href"]
            if "drive.google.com" in h:
                gdrive_links.append({
                    "text": a.get_text(strip=True)[:100],
                    "href": h[:200]
                })

        return jsonify({
            "success": True,
            "url": url,
            "page_title_meta": meta_title,
            "page_title_h1": h1_title,
            "page_title_tag": soup.title.string if soup.title else "",
            "link_opt_count": len(soup.select(".link-opt")),
            "link_opts_sample": link_opts,
            "gdrive_count": len(gdrive_links),
            "gdrive_links": gdrive_links[:10]
        })

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ========================================================
# START
# ========================================================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 3000))
    app.run(host="0.0.0.0", port=port)
