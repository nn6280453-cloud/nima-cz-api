# ========================================================
# Nima CZ API - CineSubz Scraper (Python + Playwright)
# Author: Nima
# Deploy: Railway (Docker)
# ========================================================

from flask import Flask, request, jsonify
from flask_cors import CORS
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright
import re
import os
from urllib.parse import quote

app = Flask(__name__)
CORS(app)

# ---------- CONFIG ----------
BASE_URL = "https://cinesubz.lk"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
}


def fetch_with_browser(url):
    """Fetch HTML using Playwright (real browser)."""
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
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(4000)   # wait for JS to render
        html = page.content()
        browser.close()
        return html


# ========================================================
# HEALTH CHECK
# ========================================================
@app.route("/")
def home():
    return jsonify({
        "status": "ok",
        "name": "Nima CZ API",
        "base_url": BASE_URL,
        "engine": "playwright",
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
        # Try multiple search URL patterns
        test_urls = [
            f"{BASE_URL}/?s={quote(query)}",
            f"{BASE_URL}/search?q={quote(query)}",
            f"{BASE_URL}/search/{quote(query)}",
            f"{BASE_URL}/find/?q={quote(query)}",
            f"{BASE_URL}/?search={quote(query)}",
        ]

        results = []
        for url in test_urls:
            try:
                html = fetch_with_browser(url)
                soup = BeautifulSoup(html, "html.parser")

                # Count all links that look like movie URLs
                movie_links = []
                for a in soup.find_all("a", href=True):
                    href = a["href"]
                    if any(x in href.lower() for x in [
                        "/movie/", "/movies/", "/tvshow/", "/tvshows/",
                        "/watch/", "/title/", "/series/"
                    ]):
                        text = a.get_text(strip=True)
                        if text and len(text) > 2:
                            movie_links.append({
                                "title": text[:60],
                                "url": href[:120]
                            })

                # Get page title
                page_title = soup.title.string if soup.title else "no title"

                # Count divs with common movie-item classes
                item_count = len(soup.select(
                    ".movie-item, .item, .card, .module-item, .poster, article"
                ))

                results.append({
                    "url": url,
                    "page_title": page_title,
                    "movie_links_found": len(movie_links),
                    "sample_links": movie_links[:5],
                    "item_count": item_count,
                    "content_length": len(html),
                })
            except Exception as e:
                results.append({"url": url, "error": str(e)})

        return jsonify({
            "query": query,
            "results": results
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
        html = fetch_with_browser(search_url)
        soup = BeautifulSoup(html, "html.parser")

        results = []
        seen_urls = set()

        # CineSubz uses .module-item or .item-box
        for item in soup.select(".module-item, .item-box, .result-item, .movie-item, .display-item"):
            link_el = item.find("a", href=True)
            if not link_el:
                continue

            link = link_el["href"]
            if BASE_URL not in link or link in seen_urls:
                continue

            title_el = (
                item.select_one(".title a")
                or item.select_one("h2 a")
                or item.select_one("h3 a")
                or item.select_one(".entry-title a")
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

        # Fallback
        if not results:
            for a in soup.find_all("a", href=True):
                href = a["href"]
                if BASE_URL not in href or href in seen_urls:
                    continue
                if "/movies/" not in href and "/tvshows/" not in href:
                    continue
                text = a.get_text(strip=True)
                if len(text) < 3:
                    continue
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
            "hint": "Playwright browser error"
        }), 500


# ========================================================
# 2. MOVIE DETAILS ENDPOINT
# ========================================================
@app.route("/api/movie")
def movie():
    movie_url = request.args.get("url")
    if not movie_url:
        return jsonify({"success": False, "error": 'Parameter "url" is required'}), 400

    try:
        html = fetch_with_browser(movie_url)
        soup = BeautifulSoup(html, "html.parser")

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

        full_text = soup.get_text(" ", strip=True)
        year_match = re.search(r"\b(19|20)\d{2}\b", full_text)
        year = year_match.group(0) if year_match else "N/A"

        rating_match = re.search(r"IMDb[:\s]*([\d.]+)", full_text, re.IGNORECASE)
        rating = rating_match.group(1) if rating_match else "N/A"

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
