import os
import re
import asyncio
import json
import uuid
import tempfile
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

app = FastAPI(title="Video Downloader")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = Path(__file__).parent
STATIC_DIR = BASE_DIR / "static"
DOWNLOAD_DIR = BASE_DIR / "downloads"
DOWNLOAD_DIR.mkdir(exist_ok=True)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# Detect ffmpeg at startup for format merging
HAS_FFMPEG = False
try:
    import subprocess
    # Try PATH first
    r = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True, timeout=3)
    HAS_FFMPEG = r.returncode == 0
except Exception:
    # Try common Windows install locations
    for candidate in [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Links" / "ffmpeg.exe",
        Path("C:/ProgramData/chocolatey/bin/ffmpeg.exe"),
        Path("C:/ffmpeg/bin/ffmpeg.exe"),
    ]:
        if candidate.exists():
            try:
                r = subprocess.run([str(candidate), "-version"], capture_output=True, text=True, timeout=3)
                HAS_FFMPEG = r.returncode == 0
                if HAS_FFMPEG:
                    os.environ["PATH"] = str(candidate.parent) + os.pathsep + os.environ.get("PATH", "")
            except Exception:
                pass
            break

PLATFORM_PATTERNS = {
    "YouTube": [
        r"(?:https?://)?(?:www\.|m\.|music\.)?youtube\.com/watch\?v=",
        r"(?:https?://)?(?:www\.|m\.|music\.)?youtube\.com/shorts/",
        r"(?:https?://)?(?:www\.|m\.|music\.)?youtube\.com/live/",
        r"(?:https?://)?(?:www\.|m\.|music\.)?youtube\.com/embed/",
        r"(?:https?://)?youtu\.be/",
    ],
    "抖音": [
        r"(?:https?://)?(?:www\.)?douyin\.com/",
        r"(?:https?://)?v\.douyin\.com/",
        r"(?:https?://)?(?:www\.)?iesdouyin\.com/",
        r"iesdouyin\.com/share/video/",
    ],
    "TikTok": [
        r"(?:https?://)?(?:www\.)?tiktok\.com/",
        r"(?:https?://)?vm\.tiktok\.com/",
    ],
    "Bilibili": [
        r"(?:https?://)?(?:www\.)?bilibili\.com/video/",
        r"(?:https?://)?(?:www\.)?bilibili\.com/bangumi/",
        r"(?:https?://)?b23\.tv/",
        r"(?:https?://)?(?:www\.)?bilibili\.com/list/",
    ],
    "Instagram": [
        r"(?:https?://)?(?:www\.)?instagram\.com/(?:p|reel|tv|reels)/",
    ],
    "Kuaishou": [
        r"(?:https?://)?(?:www\.)?kuaishou\.com/",
        r"(?:https?://)?v\.kuaishou\.com/",
    ],
    "微博": [
        r"(?:https?://)?(?:www\.)?weibo\.com/tv/",
        r"(?:https?://)?video\.weibo\.com/",
    ],
    "视频号": [
        r"(?:https?://)?(?:www\.)?weixin\.qq\.com/",
    ],
    "小红书": [
        r"(?:https?://)?(?:www\.)?xiaohongshu\.com/",
        r"(?:https?://)?xhslink\.com/",
    ],
}

# Standard desktop UA — yt-dlp manages per-platform UA internally.
# Avoid mobile UA as it causes Bilibili et al. to redirect to mobile subdomains
# which yt-dlp's extractor doesn't support.
DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0.0.0 Safari/537.36"
)

# Platform-specific extractor args to maximize no-login success
EXTRACTOR_ARGS = {
    "抖音": "douyin:web_device_id=auto",
    "TikTok": "tiktok:app_version=34.1.0;manifest_app_version=34.1.0",
}

# —— Cookie auto-extraction for platforms that require them (抖音 etc.) ——
COOKIES_FILE: Optional[str] = None

def _extract_cookies():
    """Try to get browser cookies for known cookie-needy platforms
    and write them to a temp Netscape-format file."""
    domains = ["douyin.com", "iesdouyin.com"]
    try:
        import browser_cookie3
        jar = browser_cookie3.chrome(domain_name="douyin.com")
        cookies = list(jar)
        if not cookies:
            jar = browser_cookie3.edge(domain_name="douyin.com")
            cookies = list(jar)
    except Exception:
        return None

    if not cookies:
        return None

    fd, path = tempfile.mkstemp(suffix=".txt", prefix="dy_cookies_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("# Netscape HTTP Cookie File\n")
            for c in cookies:
                domain = c.domain if c.domain.startswith(".") else "." + c.domain
                f.write(f"{domain}\tTRUE\t/\tFALSE\t0\t{c.name}\t{c.value}\n")
        return path
    except Exception:
        os.unlink(path)
        return None

COOKIES_FILE = _extract_cookies()


def extract_url(text: str) -> str:
    """Extract the first recognizable video URL from pasted text."""
    patterns = [
        # YouTube
        r'https?://(?:www\.|m\.|music\.)?youtube\.com/\S+',
        r'https?://youtu\.be/\S+',
        # 抖音
        r'https?://(?:www\.)?douyin\.com/\S+',
        r'https?://v\.douyin\.com/\S+',
        r'https?://(?:www\.)?iesdouyin\.com/\S+',
        # TikTok
        r'https?://(?:www\.)?tiktok\.com/\S+',
        r'https?://vm\.tiktok\.com/\S+',
        # Bilibili
        r'https?://(?:www\.)?bilibili\.com/\S+',
        r'https?://b23\.tv/\S+',
        # Instagram
        r'https?://(?:www\.)?instagram\.com/\S+',
        # Kuaishou
        r'https?://(?:www\.)?kuaishou\.com/\S+',
        r'https?://v\.kuaishou\.com/\S+',
        # 微博
        r'https?://video\.weibo\.com/\S+',
        r'https?://(?:www\.)?weibo\.com/\S+',
        # 视频号
        r'https?://(?:www\.)?weixin\.qq\.com/\S+',
        # 小红书
        r'https?://(?:www\.)?xiaohongshu\.com/\S+',
        r'https?://xhslink\.com/\S+',
    ]
    for p in patterns:
        match = re.search(p, text, re.IGNORECASE)
        if match:
            url = match.group(0)
            # Strip trailing punctuation and common non-URL characters
            url = re.sub(r'[，,。.?？！!、\s"\')\]}>]+$', '', url)
            return url
    # Fallback: try to find any http(s) URL
    fallback = re.search(r'https?://[^\s<>"\'\]\[{}|\\^`]+', text, re.IGNORECASE)
    if fallback:
        url = fallback.group(0)
        url = re.sub(r'[，,。.?？！!、\s"\')\]}>]+$', '', url)
        return url
    return text.strip()


def detect_platform(url: str) -> Optional[str]:
    for platform, patterns in PLATFORM_PATTERNS.items():
        for p in patterns:
            if re.search(p, url, re.IGNORECASE):
                return platform
    return None


def build_ytdlp_args(url: str, platform: str, extra_args: list = None) -> list:
    """Build yt-dlp command args with best-effort settings."""
    cmd = [
        "yt-dlp",
        "--user-agent", DEFAULT_UA,
        "--no-playlist",
        "--no-warnings",
    ]
    # Add cookies if available (抖音/TikTok need them)
    if COOKIES_FILE:
        cmd.extend(["--cookies", COOKIES_FILE])
    # Add platform-specific extractor args
    ea = EXTRACTOR_ARGS.get(platform)
    if ea:
        cmd.extend(["--extractor-args", ea])
    if extra_args:
        cmd.extend(extra_args)
    cmd.append(url)
    return cmd


def friendly_error(err_msg: str, url: str) -> str:
    """Translate yt-dlp errors to user-friendly messages."""
    msg = err_msg[:800]
    if "Unsupported URL" in msg:
        return "该链接格式暂不支持下载，可能是链接已过期或不完整。\n请尝试：\n1. 在浏览器中打开确认链接有效\n2. 只粘贴纯视频链接（不要带多余文字）\n3. 确认是视频页面链接而非频道/主页链接"
    if "cookies" in msg.lower() or "Fresh cookies" in msg or "Login" in msg or "login required" in msg.lower():
        if "douyin" in msg.lower():
            return ("抖音需要浏览器登录信息才能下载。\n\n"
                    "解决办法（任选一种）：\n"
                    "1. 在 Chrome 浏览器中登录抖音账号，然后重启本工具即可自动获取 cookies\n"
                    "2. 安装 EditThisCookie 插件导出 cookies.txt，放在本目录下\n"
                    "3. 换个公开 B站视频试试（B站无需登录即可下载）")
        return "该视频需要登录才能下载。这是平台限制，目前无法绕过。建议换一个公开视频链接试试。"
    if "Video unavailable" in msg:
        return "该视频不可用（可能已被删除或设为私密）"
    if "Private video" in msg:
        return "该视频为私密视频，无法访问"
    if "HTTP Error 403" in msg:
        return "访问被拒绝（403），该平台已屏蔽下载请求"
    if "HTTP Error 404" in msg:
        return "链接不存在（404），请检查链接是否正确"
    if "is not a valid URL" in msg:
        return "链接格式不正确，请只粘贴纯视频链接地址"
    if "This video is only available to Music Premium members" in msg:
        return "该视频仅限 YouTube Music Premium 会员观看"
    if "Sign in to confirm your age" in msg:
        return "该视频有年龄限制，需要登录验证"
    if "No video formats found" in msg:
        return "未找到可下载的视频格式，可能视频已下架"
    if "requested format not available" in msg.lower():
        return "所选画质不可用，请尝试其他画质"
    if "This video is not available" in msg:
        return "该视频在当前地区不可用"
    if "connection" in msg.lower() or "timeout" in msg.lower() or "timed out" in msg.lower():
        return "网络连接失败，请检查网络后重试"
    # Truncate very long error messages
    if len(msg) > 300:
        msg = msg[:300] + "..."
    return msg


def _resolve_short_url_sync(url: str) -> str:
    """Resolve short URL redirects using Python's built-in urllib (no external curl needed).

    yt-dlp handles short links natively, so this is a best-effort enhancement
    to get cleaner full URLs for display and more reliable platform detection.
    """
    import urllib.request
    import ssl

    if not any(d in url for d in ("v.douyin.com/", "iesdouyin.com/", "b23.tv/")):
        return url

    # Create a permissive SSL context (short link services often have cert issues)
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    req = urllib.request.Request(url, method="GET")
    req.add_header("User-Agent", DEFAULT_UA)

    try:
        resp = urllib.request.urlopen(req, timeout=8, context=ctx)
        final_url = resp.geturl()  # URL after following all redirects
        resp.close()
        if final_url and final_url != url:
            # Douyin: extract video ID from redirected URL
            match = re.search(r"iesdouyin\.com/share/video/(\d+)", final_url)
            if match:
                return f"https://www.douyin.com/video/{match.group(1)}"
            # Bilibili: strip query params from redirected URL
            if "bilibili.com" in final_url:
                return final_url.split("?")[0]
            return final_url
    except Exception:
        pass
    return url


async def resolve_short_url(url: str) -> str:
    """Async wrapper: run synchronous URL resolution in a thread."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _resolve_short_url_sync, url)


def parse_resolution(res: str) -> int:
    match = re.search(r"(\d+)p", res)
    return int(match.group(1)) if match else 0


@app.get("/api/info")
async def get_video_info(url: str = Query(..., description="Video URL")):
    url = extract_url(url)
    url = await resolve_short_url(url)
    platform = detect_platform(url) or "unknown"

    # Let yt-dlp decide if it can handle the URL — don't gate on our own detection
    cmd = build_ytdlp_args(url, platform if platform != "unknown" else "generic", ["--dump-json"])

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
    except asyncio.TimeoutError:
        raise HTTPException(status_code=408, detail="获取视频信息超时，可能是网络问题，请稍后重试")
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail="服务端未安装 yt-dlp")

    if proc.returncode != 0:
        err_msg = stderr.decode("utf-8", errors="replace").strip()
        # If yt-dlp says unsupported AND we also couldn't detect the platform,
        # give the user a list of known supported platforms
        if platform == "unknown" and ("Unsupported URL" in err_msg or "is not a valid URL" in err_msg or "No suitable extractor" in err_msg):
            supported = "、".join(PLATFORM_PATTERNS.keys())
            detail = f"该链接指向的平台暂不支持下载。\n当前支持的平台：{supported}\n\n如果链接来自以上平台，请确认：\n1. 粘贴的是完整视频链接\n2. 链接为公开视频（非私密或需登录）"
        else:
            detail = friendly_error(err_msg, url)
        return JSONResponse(
            status_code=400,
            content={"error": "获取信息失败", "detail": detail},
        )

    try:
        data = json.loads(stdout.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        raise HTTPException(status_code=500, detail="解析视频信息失败")

    formats = []
    seen = set()
    for f in data.get("formats", []):
        key = (f.get("ext", ""), f.get("resolution", ""), f.get("vcodec", "none"), f.get("acodec", "none"))
        if key in seen:
            continue
        seen.add(key)

        vcodec = f.get("vcodec", "none")
        acodec = f.get("acodec", "none")
        is_video = vcodec != "none"
        is_audio = acodec != "none" and vcodec == "none"

        formats.append({
            "format_id": f.get("format_id", ""),
            "ext": f.get("ext", ""),
            "resolution": f.get("resolution", ""),
            "filesize": f.get("filesize") or f.get("filesize_approx") or 0,
            "vcodec": vcodec,
            "acodec": acodec,
            "fps": f.get("fps") or 0,
            "is_video": is_video,
            "is_audio": is_audio,
        })

    formats.sort(key=lambda x: (
        0 if x["is_video"] else 1 if x["is_audio"] else 2,
        -parse_resolution(x["resolution"]),
    ))

    return {
        "title": data.get("title", "未知标题"),
        "platform": platform,
        "duration": data.get("duration", 0),
        "thumbnail": data.get("thumbnail", ""),
        "formats": formats,
        "url": url,
        "uploader": data.get("uploader", ""),
    }


PROGRESS_MAP: dict = {}


@app.get("/api/download")
async def download_video(
    url: str = Query(...),
    format_id: str = Query(default="best", description="yt-dlp format ID"),
):
    url = extract_url(url)
    url = await resolve_short_url(url)
    platform = detect_platform(url)

    task_id = uuid.uuid4().hex[:12]
    output_template = str(DOWNLOAD_DIR / f"%(title)s_%(id)s.%(ext)s")

    if format_id in ("best", "bestvideo"):
        fmt_str = "bestvideo+bestaudio/best"
    elif format_id == "bestaudio":
        fmt_str = "bestaudio/best"
    else:
        fmt_str = format_id

    extra_args = [
        "-f", fmt_str,
        "-o", output_template,
        "--merge-output-format", "mp4",
        "--newline",
        "--progress",
        "--progress-template",
        "download:%(progress._percent_str)s|%(progress._speed_str)s|%(progress._eta_str)s",
    ]

    cmd = build_ytdlp_args(url, platform if platform else "generic", extra_args)
    PROGRESS_MAP[task_id] = {"status": "downloading", "progress": 0, "speed": "", "eta": ""}

    async def run_download():
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            async def read_stderr():
                nonlocal proc
                while True:
                    line = await proc.stderr.readline()
                    if not line:
                        break
                    text = line.decode("utf-8", errors="replace").strip()
                    if text.startswith("download:"):
                        parts = text[len("download:"):].strip().split("|")
                        if len(parts) >= 1:
                            PROGRESS_MAP[task_id]["progress"] = parts[0].strip()
                        if len(parts) >= 2:
                            PROGRESS_MAP[task_id]["speed"] = parts[1].strip()
                        if len(parts) >= 3:
                            PROGRESS_MAP[task_id]["eta"] = parts[2].strip()

            async def read_stdout():
                nonlocal proc
                while True:
                    line = await proc.stdout.readline()
                    if not line:
                        break

            await asyncio.gather(read_stderr(), read_stdout())
            await proc.wait()

            if proc.returncode == 0:
                downloaded = find_latest_file(DOWNLOAD_DIR)
                if downloaded:
                    PROGRESS_MAP[task_id] = {
                        "status": "completed",
                        "file": downloaded,
                        "filename": Path(downloaded).name,
                    }
                else:
                    PROGRESS_MAP[task_id] = {"status": "error", "error": "找不到下载的文件"}
            else:
                PROGRESS_MAP[task_id] = {"status": "error", "error": "下载失败"}
        except Exception as e:
            PROGRESS_MAP[task_id] = {"status": "error", "error": str(e)}

    asyncio.create_task(run_download())
    return {"task_id": task_id, "status": "started"}


def find_latest_file(directory: Path) -> Optional[str]:
    files = [f for f in directory.iterdir() if f.is_file() and not f.name.startswith(".")]
    if not files:
        return None
    # Prefer .mp4 over .m4a when both exist (same mtime window)
    files.sort(key=lambda f: (f.stat().st_mtime, 1 if f.suffix.lower() == ".mp4" else 2), reverse=True)
    return str(files[0])


@app.get("/api/progress/{task_id}")
async def get_progress(task_id: str):
    info = PROGRESS_MAP.get(task_id)
    if not info:
        raise HTTPException(status_code=404, detail="任务不存在")
    return JSONResponse(content=info)


@app.get("/api/queue")
async def get_queue():
    return JSONResponse(content=PROGRESS_MAP)


@app.get("/downloads/{filename}")
async def serve_file(filename: str):
    file_path = DOWNLOAD_DIR / filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="文件不存在或已过期")
    return FileResponse(
        str(file_path),
        filename=filename,
        media_type="application/octet-stream",
    )


@app.get("/")
async def index():
    index_path = STATIC_DIR / "index.html"
    if index_path.exists():
        html = index_path.read_text(encoding="utf-8")
        return HTMLResponse(content=html, headers={"Cache-Control": "no-cache, no-store, must-revalidate"})
    raise HTTPException(status_code=404, detail="前端页面未找到")


if __name__ == "__main__":
    ffmpeg_status = "已安装（支持音视频合并）" if HAS_FFMPEG else "未安装（下载可能为音视频分离文件）"
    cookie_status = "已获取（抖音可下载）" if COOKIES_FILE else "未获取（抖音需在Chrome登录后重启本工具）"
    print("=" * 50)
    print("  视频下载服务已启动（无需登录）")
    print(f"  FFmpeg: {ffmpeg_status}")
    print(f"  抖音Cookie: {cookie_status}")
    print(f"  访问地址: http://localhost:16888")
    print("=" * 50)
    uvicorn.run(app, host="0.0.0.0", port=16888)
