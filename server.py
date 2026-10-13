# ========================================================
# Nima CZ API - CineSubz Scraper (Python)
# Author: Nima
# Deploy: Railway / Render
# ========================================================

from flask import Flask, request, jsonify
from flask_cors import CORS
import cloudscraper
from bs4 import BeautifulSoup
import re
import os
from urllib.parse import quote

app = Flask(__name__)
CORS(app)

# ---------- CONFIG ----------
BASE_URL = "https://cinesubz.lk"   # ✅ Updated domain

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

# Cloudscraper instance - bypasses Cloudflare
scraper = cloudscraper.create_scraper(
    browser={"browser": "chrome", "platform": "windows", "mobile": False},
    delay=5
)


def fetch(url):
    """Fetch HTML with Cloudflare bypass."""
    r = scraper.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.text


# ========================================================
# HEALTH CHECK
# ========================================================
@app.route("/")
def home():
    return jsonify({
        "status": "ok",
        "name": "Nima CZ API",
        "base_url": BASE_URL,
        "endpoints": {
            "search": "/api/search?q=movie+name",
            "movie": "/api/movie?url=movie_page_url",
            "debug": "/debug?q=avatar"
        }
    })


# ========================================================
# DEBUG ENDPOINT - Remove after testing
# ========================================================
@app.route("/debug")
def debug():
    query = request.args.get("q", "avatar")
    try:
        search_url = f"{BASE_URL}/?s={quote(query)}"
        r = scraper.get(search_url, headers=HEADERS, timeout=30)
        soup = BeautifulSoup(r.text, "html.parser")

        articles = soup.find_all("article")[:3]
        sample = []
        for a in articles:
            sample.append({
                "class": a.get("class"),
                "html_snippet": str(a)[:300]
            })

        return jsonify({
            "url": search_url,
            "status_code": r.status_code,
            "content_length": len(r.text),
            "server_header": r.headers.get("Server", "unknown"),
            "cf_ray": r.headers.get("CF-RAY", "no cloudflare"),
            "title_tag": soup.title.string if soup.title else "no title",
            "is_cloudflare_challenge": "Just a moment" in r.text or "cf-browser-verification" in r.text,
            "article_count": len(soup.find_all("article")),
            "sample_articles": sample,
            "first_500_chars": r.text[:500]
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ========================================================
# 1. SEARCH ENDPOINT
# GET /api/search?q=avatar
# ========================================================
@app.route("/api/search")
def search():
    query = request.args.get("q")
    if not query:
        return jsonify({"success": False, "error": 'Parameter "q" is required'}), 400

    try:
        search_url = f"{BASE_URL}/?s={quote(query)}"
        html = fetch(search_url)
        soup = BeautifulSoup(html, "html.parser")

        # Check Cloudflare challenge
        if "Just a moment" in html or "cf-browser-verification" in html:
            return jsonify({
                "success": False,
                "error": "Cloudflare challenge detected",
                "hint": "Site is blocking automated requests"
            }), 503

        results = []
        seen_urls = set()

        # ---- Strategy 1: DooPlay theme selectors ----
        for item in soup.select("article.item, article, .result-item, .movie-item"):
            link_el = item.find("a", href=True)
            if not link_el:
                continue

            link = link_el["href"]
            if BASE_URL not in link or link in seen_urls:
                continue

            title_el = (
                item.select_one(".details .title a")
                or item.select_one("h2 a")
                or item.select_one("h3 a")
                or item.select_one(".entry-title a")
                or item.select_one(".title")
                or link_el
            )
            title = title_el.get_text(strip=True) if title_el else ""

            img_el = item.find("img")
            poster = ""
            if img_el:
                poster = img_el.get("src") or img_el.get("data-src") or ""

            meta_text = item.get_text(" ", strip=True)
            year_match = re.search(r"\b(19|20)\d{2}\b", meta_text)
            year = year_match.group(0) if year_match else "N/A"

            rating_el = item.select_one(".rating, .imdb, .vote")
            rating = rating_el.get_text(strip=True) if rating_el else "N/A"

            if title:
                seen_urls.add(link)
                results.append({
                    "title": title,
                    "year": year,
                    "rating": rating,
                    "poster": poster,
                    "url": link
                })

        # ---- Strategy 2: Fallback ----
        if not results:
            for a in soup.find_all("a", href=True):
                href = a["href"]
                if BASE_URL not in href or href in seen_urls:
                    continue
                text = a.get_text(strip=True)
                if len(text) < 3 or len(text) > 200:
                    continue
                if any(x in href.lower() for x in ["/category/", "/tag/", "/page/", "#", "javascript:"]):
                    continue
                if any(x in href.lower() for x in ["/movie", "/tv", "/series"]):
                    seen_urls.add(href)
                    results.append({
                        "title": text,
                        "year": "N/A",
                        "rating": "N/A",
                        "poster": "",
                        "url": href
                    })

        return jsonify({
            "success": True,
            "count": len(results),
            "data": results[:20]
        })

    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e),
            "hint": "Check /debug?q=avatar"
        }), 500


# ========================================================
# 2. MOVIE DETAILS ENDPOINT
# GET /api/movie?url=...
# Returns: title, poster, description + ALL quality download links
# ========================================================
@app.route("/api/movie")
def movie():
    movie_url = request.args.get("url")
    if not movie_url:
        return jsonify({"success": False, "error": 'Parameter "url" is required'}), 400

    try:
        html = fetch(movie_url)
        soup = BeautifulSoup(html, "html.parser")

        # ---- Basic info ----
        title_el = soup.select_one("h1.entry-title, h1")
        title = title_el.get_text(strip=True) if title_el else "N/A"

        poster_el = soup.select_one(
            ".poster img, .entry-content img, article img, .splash img"
        )
        poster = ""
        if poster_el:
            poster = poster_el.get("src") or poster_el.get("data-src") or ""

        desc_el = soup.select_one(".entry-content p, .description, .wp-content p")
        description = desc_el.get_text(strip=True)[:500] if desc_el else ""

        # ---- Year / Rating ----
        full_text = soup.get_text(" ", strip=True)
        year_match = re.search(r"\b(19|20)\d{2}\b", full_text)
        year = year_match.group(0) if year_match else "N/A"

        rating_match = re.search(r"IMDb[:\s]*([\d.]+)", full_text, re.IGNORECASE)
        rating = rating_match.group(1) if rating_match else "N/A"

        # ---- Download links (ALL qualities) ----
        quality_pattern = re.compile(
            r"(480p|720p|1080p|1440p|2160p|4K|HD|FHD|HDTV|BluRay|WEB-?DL|WEBRip|HDRip|DVDRip|CAM)",
            re.IGNORECASE
        )
        download_hosts = re.compile(
            r"(download|drive\.google|mega\.nz|pixeldrain|hubcloud|workers\.dev|gdtot|filepress|filebee|streamtape|dood)",
            re.IGNORECASE
        )

        download_links = []
        seen = set()

        for a in soup.find_all("a", href=True):
            href = a["href"]
            label = a.get_text(" ", strip=True)
            if not href or href in seen:
                continue

            is_quality = (
                quality_pattern.search(label)
                or quality_pattern.search(href)
                or download_hosts.search(href)
            )

            if is_quality:
                seen.add(href)

                qmatch = quality_pattern.search(label) or quality_pattern.search(href)
                quality = qmatch.group(0).upper() if qmatch else "Download"
                quality = quality.replace("WEB-DL", "WEB-DL").replace("WEBRIP", "WEBRip")

                download_links.append({
                    "quality": quality,
                    "label": label[:80] or quality,
                    "url": href
                })

        # Sort: 4K → 1080p → 720p → 480p
        order = {"4K": 0, "2160P": 1, "1440P": 2, "1080P": 3, "720P": 4, "480P": 5}
        download_links.sort(key=lambda x: order.get(x["quality"], 99))

        return jsonify({
            "success": True,
            "data": {
                "title": title,
                "year": year,
                "rating": rating,
                "poster": poster,
                "description": description,
                "totalLinks": len(download_links),
                "downloadLinks": download_links
            }
        })

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ---------- START SERVER ----------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 3000))
    app.run(host="0.0.0.0", port=port)
