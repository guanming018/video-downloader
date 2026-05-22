FROM python:3.11-slim

# Install system deps: ffmpeg for DASH merging, build tools for curl_cffi
RUN apt-get update && apt-get install -y ffmpeg gcc python3-dev && \
    pip install --no-cache-dir yt-dlp && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install core Python dependencies + curl_cffi (for 抖音 TLS fingerprinting)
# browser_cookie3 is intentionally omitted — it requires a local browser
RUN pip install --no-cache-dir \
    "fastapi>=0.104" "uvicorn>=0.24" "aiofiles>=23" \
    "curl_cffi>=0.7"

COPY . .

EXPOSE 16888

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
