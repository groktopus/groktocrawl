#!/bin/sh
if [ "${SCRAPER_API_SOCKET:-}" != "" ]; then
    exec python -m scraper.capture_firewall
fi
# Binary downloads are not image-layer artifacts; failure preserves stock Chromium fallback.
python -m cloakbrowser install || true
exec uvicorn scraper.app:app --host 0.0.0.0 --port 8001
