FROM python:3.11-slim

# Install ffmpeg + yt-dlp (needed by yt-dlp for DASH format merging)
RUN apt-get update && apt-get install -y ffmpeg && \
    pip install --no-cache-dir yt-dlp && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies (only the core ones — curl_cffi and
# browser_cookie3 need native browser libs and won't work on Linux servers)
RUN pip install --no-cache-dir "fastapi>=0.104" "uvicorn>=0.24" "aiofiles>=23"

COPY . .

EXPOSE 16888

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
