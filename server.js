// ========================================================
// Nima CZ API - CineSubz Scraper
// Author: Nima
// Deploy: Render
// ========================================================

const express = require('express');
const axios = require('axios');
const cheerio = require('cheerio');
const cors = require('cors');

const app = express();
app.use(cors());
app.use(express.json());

// ---------- CONFIG ----------
const BASE_URL = 'https://cinesubz.co'; // CineSubz main domain
const HEADERS = {
  'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
  'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
  'Accept-Language': 'en-US,en;q=0.5'
};

// ---------- HEALTH CHECK ----------
app.get('/', (req, res) => {
  res.json({
    status: 'ok',
    name: 'Nima CZ API',
    endpoints: {
      search: '/api/search?q=movie+name',
      movie: '/api/movie?url=movie_page_url'
    }
  });
});

// ========================================================
// 1. SEARCH ENDPOINT
// GET /api/search?q=avatar
// ========================================================
app.get('/api/search', async (req, res) => {
  const query = req.query.q;
  if (!query) {
    return res.status(400).json({ success: false, error: 'Parameter "q" is required' });
  }

  try {
    const searchUrl = `${BASE_URL}/?s=${encodeURIComponent(query)}`;
    const { data } = await axios.get(searchUrl, { headers: HEADERS, timeout: 20000 });
    const $ = cheerio.load(data);

    const results = [];

    // DooPlay theme uses article.item or similar
    $('article, .result-item, .movie-item, .item').each((i, el) => {
      const $el = $(el);
      const title = $el.find('h2, h3, .title, .entry-title').first().text().trim();
      const link = $el.find('a').first().attr('href');
      const poster = $el.find('img').attr('src') || $el.find('img').attr('data-src');
      const metaText = $el.find('.meta, .year, .info').text();
      const year = metaText.match(/\d{4}/)?.[0] || 'N/A';
      const rating = $el.find('.rating, .imdb').text().trim() || 'N/A';

      if (title && link && link.includes(BASE_URL)) {
        results.push({
          title,
          year,
          rating,
          poster: poster || '',
          url: link
        });
      }
    });

    // Remove duplicates
    const unique = results.filter((v, i, a) => a.findIndex(t => t.url === v.url) === i);

    res.json({
      success: true,
      count: unique.length,
      data: unique
    });

  } catch (err) {
    res.status(500).json({
      success: false,
      error: err.message,
      hint: 'CineSubz site structure might have changed'
    });
  }
});

// ========================================================
// 2. MOVIE DETAILS ENDPOINT
// GET /api/movie?url=https://cinesubz.co/movie/...
// Returns: title, poster, description + ALL quality download links
// ========================================================
app.get('/api/movie', async (req, res) => {
  const movieUrl = req.query.url;
  if (!movieUrl) {
    return res.status(400).json({ success: false, error: 'Parameter "url" is required' });
  }

  try {
    const { data } = await axios.get(movieUrl, { headers: HEADERS, timeout: 25000 });
    const $ = cheerio.load(data);

    // ---- Basic info ----
    const title = $('h1.entry-title, h1').first().text().trim();
    const poster = $('.poster img, .entry-content img, article img').first().attr('src') || '';
    const description = $('.entry-content p, .description').first().text().trim();

    // Try to extract year, rating, genre from meta
    const fullText = $('body').text();
    const year = fullText.match(/\b(19|20)\d{2}\b/)?.[0] || 'N/A';
    const ratingMatch = fullText.match(/IMDb[:\s]*([\d.]+)/i);
    const rating = ratingMatch ? ratingMatch[1] : 'N/A';

    // ---- Download links (ALL qualities) ----
    const downloadLinks = [];
    const seen = new Set();

    $('a').each((i, el) => {
      const $el = $(el);
      const text = $el.text().trim();
      const href = $el.attr('href');
      if (!href) return;

      // Detect quality patterns
      const qualityPattern = /(480p|720p|1080p|1440p|2160p|4K|HD|FHD|HDTV|BluRay|WEB-?DL|WEBRip|HDRip|DVDRip|CAM)/i;
      const isQualityLink =
        qualityPattern.test(text) ||
        qualityPattern.test(href) ||
        /(download|dl\.|drive\.google|mega\.nz|pixeldrain|hubcloud|workers\.dev|gdtot|filepress)/i.test(href);

      if (isQualityLink && !seen.has(href)) {
        seen.add(href);

        // Extract quality from text or href
        let quality = text.match(qualityPattern)?.[0];
        if (!quality) {
          quality = href.match(qualityPattern)?.[0];
        }
        if (!quality) quality = 'Download';

        // Clean quality label
        quality = quality.toUpperCase().replace('WEB-DL', 'WEB-DL').replace('WEBRIP', 'WEBRip');

        downloadLinks.push({
          quality: quality,
          label: text.substring(0, 80) || quality,
          url: href
        });
      }
    });

    res.json({
      success: true,
      data: {
        title,
        year,
        rating,
        poster,
        description: description.substring(0, 500),
        totalLinks: downloadLinks.length,
        downloadLinks
      }
    });

  } catch (err) {
    res.status(500).json({
      success: false,
      error: err.message
    });
  }
});

// ---------- START SERVER ----------
const PORT = process.env.PORT || 3000;
app.listen(PORT, '0.0.0.0', () => {
  console.log(`✅ Nima CZ API running on port ${PORT}`);
});
