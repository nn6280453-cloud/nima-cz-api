# ========================================================
# Nima CZ API - CineSubz Scraper (Python)
# Author: Nima
# Deploy: Render / Railway
# ========================================================

from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
from bs4 import BeautifulSoup
import re
from urllib.parse import quote

app = Flask(__name__)
CORS(app)

# ---------- CONFIG ----------
BASE_URL = "https://cinesubz.co"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}


def fetch(url):
    """Fetch HTML with timeout."""
    r = requests.get(url, headers=HEADERS, timeout=20)
    r.raise_for_status()
    return r.text


# ---------- HEALTH CHECK ----------
@app.route("/")
def home():
    return jsonify({
        "status": "ok",
        "name": "Nima CZ API",
        "endpoints": {
            "search": "/api/search?q=movie+name",
            "movie": "/api/movie?url=movie_page_url"
        }
    })


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

        results = []

        # CineSubz DooPlay theme selectors - adjust if needed
        for item in soup.select("article, .result-item, .movie-item, .item"):
            title_el = item.select_one("h2, h3, .title, .entry-title")
            link_el = item.find("a")
            img_el = item.find("img")

            if not title_el or not link_el:
                continue

            title = title_el.get_text(strip=True)
            link = link_el.get("href", "")
            poster = ""
            if img_el:
                poster = img_el.get("src") or img_el.get("data-src") or ""

            meta_text = item.get_text(" ", strip=True)
            year_match = re.search(r"\b(19|20)\d{2}\b", meta_text)
            year = year_match.group(0) if year_match else "N/A"

            rating_el = item.select_one(".rating, .imdb")
            rating = rating_el.get_text(strip=True) if rating_el else "N/A"

            if title and link and BASE_URL in link:
                results.append({
                    "title": title,
                    "year": year,
                    "rating": rating,
                    "poster": poster,
                    "url": link
                })

        # Remove duplicates by URL
        seen = set()
        unique = []
        for r in results:
            if r["url"] not in seen:
                seen.add(r["url"])
                unique.append(r)

        return jsonify({
            "success": True,
            "count": len(unique),
            "data": unique
        })

    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e),
            "hint": "CineSubz site structure might have changed"
        }), 500


# ========================================================
# 2. MOVIE DETAILS ENDPOINT
# GET /api/movie?url=https://cinesubz.co/movie/...
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

        poster_el = soup.select_one(".poster img, .entry-content img, article img")
        poster = ""
        if poster_el:
            poster = poster_el.get("src") or poster_el.get("data-src") or ""

        desc_el = soup.select_one(".entry-content p, .description")
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
            r"(download|drive\.google|mega\.nz|pixeldrain|hubcloud|workers\.dev|gdtot|filepress)",
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
    import os
    port = int(os.environ.get("PORT", 3000))
    app.run(host="0.0.0.0", port=port)
