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
BASE_URL = "https://cinesubz.net"
SEARCH_URL = "https://cinesubz.lk"
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
        page.wait_for_timeout(4000)
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
            "resolve": "/api/resolve?url=zt_links_url"
        }
    })


# ========================================================
# 1. SEARCH ENDPOINT
# ========================================================
@app.route("/api/search")
def search():
    query = request.args.get("q")
    if not query:
        return jsonify({"success": False, "error": 'Parameter "q" is required'}), 400

    try:
        search_url = f"{SEARCH_URL}/?s={quote(query)}"
        html = fetch_with_browser(search_url)
        soup = BeautifulSoup(html, "html.parser")

        results = []
        seen_urls = set()

        for item in soup.select(".item-box"):
            link_el = item.find("a", href=True)
            if not link_el:
                continue

            link = link_el["href"]
            if link in seen_urls:
                continue

            title_el = (
                item.select_one(".item-desc-title")
                or item.select_one(".item-desc-hl")
                or item.select_one(".item-desc")
            )
            title = title_el.get_text(strip=True) if title_el else ""

            img_el = item.select_one(".mli-thumb img, .thumb img, img")
            poster = ""
            if img_el:
                poster = img_el.get("src") or img_el.get("data-src") or ""

            rating_el = item.select_one(".imdb-score, .rating1, .imdb-rating-badge")
            rating = rating_el.get_text(strip=True) if rating_el else "N/A"
            if rating and rating != "N/A":
                rating = re.sub(r"[^\d.]", "", rating) or "N/A"

            year_match = re.search(r"\b(19|20)\d{2}\b", link) or re.search(
                r"\b(19|20)\d{2}\b", title
            )
            year = year_match.group(0) if year_match else "N/A"

            quality_el = item.select_one(".badge-quality-corner")
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

        # Title
        title = "N/A"
        og_title = soup.find("meta", property="og:title")
        if og_title and og_title.get("content"):
            title = og_title["content"].strip()

        if title == "N/A" or not title:
            title_el = (
                soup.select_one("h1.entry-title")
                or soup.select_one(".sheader h1")
                or soup.select_one("article h1")
                or soup.select_one(".entry-header h1")
                or soup.select_one(".post-title")
            )
            if title_el:
                title = title_el.get_text(strip=True)

        if title == "N/A" or not title:
            page_title = soup.title.string if soup.title else ""
            if page_title:
                title = page_title.split("|")[0].strip()

        title = re.sub(r"\s*\|.*$", "", title).strip()
        title = re.sub(r"\s*[-–]\s*CineSubz.*$", "", title, flags=re.IGNORECASE).strip()

        # Poster
        poster = ""
        og_image = soup.find("meta", property="og:image")
        if og_image and og_image.get("content"):
            poster = og_image["content"].strip()
        else:
            poster_el = (
                soup.select_one(".mli-thumb img")
                or soup.select_one(".poster img")
                or soup.select_one("article img")
            )
            if poster_el:
                poster = poster_el.get("src") or poster_el.get("data-src") or ""

        # Description
        description = ""
        og_desc = soup.find("meta", property="og:description")
        if og_desc and og_desc.get("content"):
            description = og_desc["content"].strip()[:500]
        else:
            desc_el = soup.select_one(
                ".entry-content p, .description, .wp-content p, .item-desc"
            )
            if desc_el:
                description = desc_el.get_text(strip=True)[:500]

        # Year
        year_match = (
            re.search(r"\b(19|20)\d{2}\b", movie_url)
            or re.search(r"\b(19|20)\d{2}\b", title)
        )
        year = year_match.group(0) if year_match else "N/A"

        # Rating
        rating = "N/A"
        rating_el = soup.select_one(
            ".imdb-score, .imdb-rating-badge .score, .rating1 .score"
        )
        if rating_el:
            rating_text = rating_el.get_text(strip=True)
            rating_match = re.search(r"(\d+\.\d+)", rating_text)
            if rating_match:
                rating = rating_match.group(1)

        if rating == "N/A":
            full_text = soup.get_text(" ", strip=True)
            rating_match = re.search(r"IMDb[:\s]*([\d]+\.[\d]+)", full_text)
            if rating_match:
                rating = rating_match.group(1)

        # Download links (only /zt-links/)
        download_links = []
        seen = set()

        for a in soup.find_all("a", href=True):
            href = a["href"]
            label = a.get_text(" ", strip=True)

            if "/zt-links/" not in href:
                continue
            if href in seen:
                continue

            seen.add(href)

            res_match = re.search(r"\b(\d{3,4}p|4K|2160p)\b", label, re.IGNORECASE)
            if res_match:
                quality = res_match.group(0).upper()
            else:
                src_match = re.search(
                    r"\b(BluRay|WEB-?DL|WEBRip|HDRip|HDTV|DVDRip|CAM|HD|FHD)\b",
                    label, re.IGNORECASE
                )
                quality = src_match.group(0).upper() if src_match else "Download"

            size_match = re.search(r"(\d+(?:\.\d+)?\s*(?:MB|GB|KB))", label, re.IGNORECASE)
            size = size_match.group(1).strip() if size_match else ""

            download_links.append({
                "quality": quality,
                "size": size,
                "label": label[:120] or quality,
                "url": href
            })

        order = {
            "4K": 0, "2160P": 1, "1440P": 2, "1080P": 3,
            "720P": 4, "480P": 5, "360P": 6
        }
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


# ========================================================
# 3. RESOLVE ENDPOINT (NEW)
# GET /api/resolve?url=https://cinesubz.net/zt-links/xxx/
# ========================================================
@app.route("/api/resolve")
def resolve():
    source_url = request.args.get("url")
    if not source_url:
        return jsonify({"success": False, "error": 'Parameter "url" is required'}), 400

    try:
        html = fetch_with_browser(source_url)
        soup = BeautifulSoup(html, "html.parser")

        host_pattern = re.compile(
            r"(drive\.google|mega\.nz|pixeldrain|hubcloud|workers\.dev|"
            r"gdtot|filepress|filebee|streamtape|dood|1fichier|mediafire|"
            r"dropbox|gdflix|katdrive|kolop|sonic-cloud|fastdl|"
            r"gdsharing|hubdrive|sfile|shareus|gdrive|gdlink)",
            re.IGNORECASE
        )
        ext_pattern = re.compile(r"\.(mkv|mp4|avi|mov|zip|rar|7z|m4v|webm)(\?|$)", re.IGNORECASE)

        hosts = []
        seen = set()

        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            label = a.get_text(" ", strip=True)

            if not href or href in seen:
                continue
            if href.startswith("#") or href.startswith("javascript:"):
                continue
            if "cinesubz" in href and "/zt-links/" not in href:
                continue

            is_host = host_pattern.search(href) or host_pattern.search(label)
            is_direct = ext_pattern.search(href)

            if is_host or is_direct:
                seen.add(href)
                hosts.append({
                    "label": label[:100] if label else "Download",
                    "url": href
                })

        # Fallback: any external link
        if not hosts:
            for a in soup.find_all("a", href=True):
                href = a["href"].strip()
                if not href.startswith("http"):
                    continue
                if "cinesubz" in href:
                    continue
                if href in seen:
                    continue
                seen.add(href)
                hosts.append({
                    "label": a.get_text(" ", strip=True)[:100] or "Link",
                    "url": href
                })

        is_blocked = "Just a moment" in html or "cf-browser-verification" in html
        page_title = soup.title.string.strip() if soup.title else ""

        return jsonify({
            "success": True,
            "data": {
                "source": source_url,
                "pageTitle": page_title,
                "isBlocked": is_blocked,
                "totalHosts": len(hosts),
                "hosts": hosts
            }
        })

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ---------- START SERVER ----------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 3000))
    app.run(host="0.0.0.0", port=port)
