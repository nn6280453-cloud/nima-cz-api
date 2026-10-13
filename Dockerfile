FROM python:3.12-slim

RUN apt-get update && apt-get install -y \
    wget ca-certificates fonts-liberation fonts-unifont \
    libasound2 libatk-bridge2.0-0 libatk1.0-0 libatspi2.0-0 \
    libcups2 libdbus-1-3 libdrm2 libgbm1 libgtk-3-0 libnspr4 \
    libnss3 libx11-xcb1 libxcomposite1 libxdamage1 libxfixes3 \
    libxkbcommon0 libxrandr2 xdg-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN playwright install chromium
COPY . .

ENV PORT=3000
CMD gunicorn server:app --bind 0.0.0.0:$PORT --timeout 180 --workers 1 --threads 4 --worker-class gthread
