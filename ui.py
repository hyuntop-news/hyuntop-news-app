import html
import hmac
import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request
import wave
import zipfile
from datetime import datetime
from pathlib import Path

import streamlit as st
from morning_news_mailer import (
    create_derivative_content_from_blog,
    create_youtube_pptx,
    load_env_file,
    run_mailer,
)


BASE_DIR = Path(__file__).resolve().parent
VIDEO_VENDOR_DIR = BASE_DIR / "video_vendor"
VENDOR_DIR = BASE_DIR / "vendor"
IMAGE_ASSET_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
VIDEO_ASSET_EXTS = {".mp4", ".mov", ".m4v"}

SETTINGS_PATH = BASE_DIR / "settings.json"
NEWS_SCRIPT = BASE_DIR / "Send-MorningNews.ps1"
SCHEDULE_SCRIPT = BASE_DIR / "schedule_daily.ps1"
LOG_PATH = BASE_DIR / "logs" / "morning-news.log"
load_env_file()


def normalize_saved_text(value) -> str:
    if isinstance(value, dict):
        title = str(value.get("title") or value.get("headline") or "").strip()
        content = str(
            value.get("content")
            or value.get("body")
            or value.get("text")
            or value.get("post")
            or ""
        ).strip()
        if title and content and not content.lstrip().startswith("#"):
            return f"# {title}\n\n{content}".strip()
        return (content or title).strip()
    if isinstance(value, list):
        return "\n\n".join(normalize_saved_text(item) for item in value if item).strip()

    text = str(value or "").strip()
    if text.startswith("{") and ("'content'" in text or '"content"' in text or "'body'" in text or '"body"' in text):
        try:
            return normalize_saved_text(json.loads(text))
        except json.JSONDecodeError:
            try:
                import ast

                return normalize_saved_text(ast.literal_eval(text))
            except (ValueError, SyntaxError):
                pass
    return text.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\\t", "\t").strip()

DEFAULT_SETTINGS = {
    "news_query": "과학",
    "news_limit": 5,
    "recipient_email": "",
    "notification_channel": "email",
    "schedule_time": "07:00",
    "blog_enabled": True,
    "blog_pick_index": 1,
    "blog_draft_dir": "blog_drafts",
    "content_candidate_limit": 10,
    "low_cost_mode": False,
    "retry_count": 3,
    "retry_delay_seconds": 3,
    "request_timeout_seconds": 10,
    "error_email_enabled": True,
}


def load_settings() -> dict:
    if not SETTINGS_PATH.exists():
        return DEFAULT_SETTINGS.copy()

    with SETTINGS_PATH.open("r", encoding="utf-8-sig") as file:
        loaded = json.load(file)

    settings = DEFAULT_SETTINGS.copy()
    settings.update(loaded)
    return settings


def save_settings(settings: dict) -> None:
    with SETTINGS_PATH.open("w", encoding="utf-8") as file:
        json.dump(settings, file, ensure_ascii=False, indent=2)


def run_powershell(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", *args],
        cwd=BASE_DIR,
        text=True,
        capture_output=True,
        timeout=120,
    )


def append_activity(message: str) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with LOG_PATH.open("a", encoding="utf-8") as file:
        file.write(f"[{timestamp}] [INFO] {message}\n")


def read_recent_logs(limit: int = 8) -> list[str]:
    log_paths = [
        LOG_PATH,
        BASE_DIR / "streamlit-local.log",
        BASE_DIR / "streamlit-local.out.log",
        BASE_DIR / "streamlit-local.err.log",
    ]
    entries: list[tuple[float, int, str]] = []
    for path in log_paths:
        if not path.exists():
            continue
        try:
            modified = path.stat().st_mtime
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line_number, line in enumerate(lines):
            clean = line.strip()
            if clean and not looks_broken_text(clean):
                entries.append((modified, line_number, clean))

    entries.sort(key=lambda item: (item[0], item[1]))
    return [line for _, _, line in entries[-limit:]] or ["아직 실행 기록이 없습니다."]


def short_error_message(message: str) -> str:
    text = " ".join(str(message or "").split())
    return text[:180] + ("..." if len(text) > 180 else "")


def summarize_run_for_log(result: dict) -> str:
    sent = result.get("sent", 0)
    recipient = result.get("recipient", "")
    package = result.get("content_package")
    content_message = result.get("content_message", "")
    if sent and package:
        return f"Run finished successfully. Sent {sent} news items to {recipient}; content package created."
    if sent:
        suffix = f"; {short_error_message(content_message)}" if content_message else ""
        return f"Run finished with mail only. Sent {sent} news items to {recipient}{suffix}"
    return f"Run finished without sending mail. {short_error_message(result.get('message', 'No news sent'))}"


def summarize_exception_for_log(exc: Exception) -> str:
    return f"Run failed. {short_error_message(str(exc))}"


def looks_broken_text(text: str) -> bool:
    markers = ["?댁", "?쒕", "?먮", "?좏", "?꾨", "異", "諛", "遺", "寃", "蹂", "吏", "�"]
    if any(marker in text for marker in markers):
        return True
    return text.count("???") >= 2


def rebuild_derivative_files_from_blog(package_dir: Path) -> None:
    blog_text = read_text_if_exists(package_dir / "01-blog-post.md")
    derivatives = create_derivative_content_from_blog(
        blog_text,
        title=package_dir.name,
        source="블로그 글 기준 복구",
    )
    (package_dir / "02-tistory-post.md").write_text(derivatives["tistory_post"], encoding="utf-8")
    (package_dir / "03-thread-post.txt").write_text(derivatives["thread_post"], encoding="utf-8")
    (package_dir / "04-youtube-slides.md").write_text(derivatives["slide_script"], encoding="utf-8")
    (package_dir / "05-vrew-script.txt").write_text(derivatives["vrew_script"], encoding="utf-8")
    create_youtube_pptx(derivatives["slide_script"], package_dir / "06-youtube-slides.pptx", package_dir.name)
    shutil.rmtree(package_dir / "video_package", ignore_errors=True)
    for old_zip in package_dir.glob("*-video-package.zip"):
        old_zip.unlink(missing_ok=True)


def get_recent_draft(settings: dict) -> str:
    draft_dir = BASE_DIR / str(settings.get("blog_draft_dir") or "blog_drafts")
    if not draft_dir.exists():
        return "콘텐츠 없음"

    packages = sorted(
        [
            path
            for path in draft_dir.iterdir()
            if path.is_dir()
            and not path.name.endswith("news-content")
            and "테스트" not in path.name
            and not looks_broken_text(path.name)
        ],
        key=content_package_mtime,
        reverse=True,
    )
    if not packages:
        return "콘텐츠 없음"

    return packages[0].name


def get_recent_content_package(settings: dict) -> Path | None:
    draft_dir = BASE_DIR / str(settings.get("blog_draft_dir") or "blog_drafts")
    if not draft_dir.exists():
        return None

    packages = sorted(
        [
            path
            for path in draft_dir.iterdir()
            if path.is_dir()
            and not path.name.endswith("news-content")
            and "테스트" not in path.name
            and not looks_broken_text(path.name)
        ],
        key=content_package_mtime,
        reverse=True,
    )
    return packages[0] if packages else None


def content_package_mtime(path: Path) -> float:
    content_files = [
        path / "01-blog-post.md",
        path / "02-tistory-post.md",
        path / "03-thread-post.txt",
        path / "04-youtube-slides.md",
        path / "05-vrew-script.txt",
        path / "06-youtube-slides.pptx",
    ]
    times = [file.stat().st_mtime for file in content_files if file.exists()]
    times.append(path.stat().st_mtime)
    return max(times)


def read_text_if_exists(path: Path) -> str:
    if not path.exists():
        return "아직 저장된 내용이 없습니다."
    return normalize_saved_text(path.read_text(encoding="utf-8", errors="replace"))


def extract_markdown_title(text: str, fallback: str) -> str:
    for line in text.splitlines():
        clean = line.strip()
        if clean.startswith("#"):
            return clean.lstrip("#").strip()[:90] or fallback
    return fallback


def parse_slide_headings(slide_text: str) -> list[str]:
    headings = []
    current = ""
    for line in slide_text.splitlines():
        clean = line.strip()
        if clean.startswith("## 슬라이드"):
            current = clean.lstrip("#").strip()
        elif current and clean and clean not in {"화면 문구", "내레이션"}:
            headings.append(f"{current}: {clean}")
            current = ""
    return headings[:6]


def safe_video_name(name: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|]+', " ", name)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:60] or "youtube-video-package"


def get_slide_asset_dir(package_dir: Path) -> Path:
    return package_dir / "video_assets"


def get_config_value(name: str) -> str:
    value = os.getenv(name, "").strip()
    if value:
        return value
    try:
        return str(st.secrets.get(name, "")).strip()
    except Exception:
        return ""


def get_slide_asset(asset_dir: Path, index: int, allowed_exts: set[str]) -> Path | None:
    for ext in sorted(allowed_exts):
        path = asset_dir / f"slide_{index:02}{ext}"
        if path.exists():
            return path
    return None


def save_slide_asset(package_dir: Path, index: int, uploaded_file) -> Path:
    suffix = Path(uploaded_file.name).suffix.lower()
    if suffix not in IMAGE_ASSET_EXTS | VIDEO_ASSET_EXTS:
        raise ValueError("이미지 또는 MP4 영상 파일만 넣을 수 있습니다.")

    asset_dir = get_slide_asset_dir(package_dir)
    asset_dir.mkdir(parents=True, exist_ok=True)
    for old in asset_dir.glob(f"slide_{index:02}.*"):
        old.unlink(missing_ok=True)

    output_path = asset_dir / f"slide_{index:02}{suffix}"
    output_path.write_bytes(uploaded_file.getbuffer())
    return output_path


def guess_image_query(slide: dict[str, str], fallback: str) -> str:
    text = " ".join(
        part.strip()
        for part in [slide.get("screen", ""), slide.get("title", ""), fallback]
        if part and part.strip()
    )
    text = re.sub(r"[#*_`\"'()\[\]{}<>|]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:80] or fallback or "news"


def download_pexels_image(query: str, output_path: Path, api_key: str) -> None:
    params = urllib.parse.urlencode(
        {
            "query": query,
            "per_page": 1,
            "orientation": "landscape",
            "locale": "ko-KR",
        }
    )
    request = urllib.request.Request(
        f"https://api.pexels.com/v1/search?{params}",
        headers={"Authorization": api_key, "User-Agent": "HYUNTOP-News-Dashboard/1.0"},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        payload = json.loads(response.read().decode("utf-8"))

    photos = payload.get("photos") or []
    if not photos:
        raise RuntimeError(f"'{query}' 검색 결과가 없습니다.")

    src = photos[0].get("src", {})
    image_url = src.get("landscape") or src.get("large") or src.get("original")
    if not image_url:
        raise RuntimeError("이미지 주소를 찾지 못했습니다.")

    image_request = urllib.request.Request(image_url, headers={"User-Agent": "HYUNTOP-News-Dashboard/1.0"})
    with urllib.request.urlopen(image_request, timeout=30) as response:
        output_path.write_bytes(response.read())


def auto_fill_slide_images(package_dir: Path) -> list[str]:
    api_key = get_config_value("PEXELS_API_KEY")
    if not api_key:
        raise RuntimeError("PEXELS_API_KEY가 없습니다. 로컬 .env 또는 Streamlit Secrets에 추가해 주세요.")

    slide_text = read_text_if_exists(package_dir / "04-youtube-slides.md")
    slides = parse_slide_blocks_for_assets(slide_text)
    if not slides:
        raise RuntimeError("유튜브 슬라이드 대본이 없어 자동 이미지를 찾을 수 없습니다.")

    asset_dir = get_slide_asset_dir(package_dir)
    asset_dir.mkdir(parents=True, exist_ok=True)
    title = extract_markdown_title(read_text_if_exists(package_dir / "01-blog-post.md"), package_dir.name)

    results: list[str] = []
    for index, slide in enumerate(slides[:6], 1):
        if get_slide_asset(asset_dir, index, IMAGE_ASSET_EXTS | VIDEO_ASSET_EXTS):
            results.append(f"슬라이드 {index}: 기존 파일 유지")
            continue
        query = guess_image_query(slide, title)
        output_path = asset_dir / f"slide_{index:02}.jpg"
        download_pexels_image(query, output_path, api_key)
        results.append(f"슬라이드 {index}: 자동 이미지 추가")
    return results


def parse_slide_blocks_for_assets(slide_text: str) -> list[dict[str, str]]:
    slides: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    mode = ""
    for line in slide_text.splitlines():
        clean = line.strip().lstrip("-").strip()
        if not clean:
            continue
        label = clean.rstrip(":：").strip()
        if clean.startswith("## 슬라이드"):
            if current:
                slides.append(current)
            current = {"title": clean.lstrip("# ").strip(), "screen": "", "narration": ""}
            mode = ""
        elif current and label == "화면 문구":
            mode = "screen"
        elif current and label == "내레이션":
            mode = "narration"
        elif current and clean.startswith("화면 문구:"):
            mode = "screen"
            current["screen"] = (current["screen"] + "\n" + clean.split(":", 1)[1].strip()).strip()
        elif current and clean.startswith("내레이션:"):
            mode = "narration"
            current["narration"] = (current["narration"] + "\n" + clean.split(":", 1)[1].strip()).strip()
        elif current and mode == "screen":
            current["screen"] = (current["screen"] + "\n" + clean).strip()
        elif current and mode == "narration":
            current["narration"] = (current["narration"] + "\n" + clean).strip()
    if current:
        slides.append(current)
    return slides[:6]


def wrap_text_by_width(draw, text: str, font, max_width: int) -> list[str]:
    lines: list[str] = []
    for raw_line in text.splitlines():
        words = raw_line.split()
        if not words:
            lines.append("")
            continue
        line = ""
        for word in words:
            candidate = f"{line} {word}".strip()
            width = draw.textbbox((0, 0), candidate, font=font)[2]
            if width <= max_width:
                line = candidate
            else:
                if line:
                    lines.append(line)
                line = word
        if line:
            lines.append(line)
    return lines


def create_slide_images(slide_text: str, output_dir: Path, asset_dir: Path | None = None) -> list[Path]:
    from PIL import Image, ImageDraw, ImageFont, ImageOps

    slides = parse_slide_blocks_for_assets(slide_text)
    output_dir.mkdir(parents=True, exist_ok=True)

    def load_font(size: int, bold: bool = False):
        candidates = [
            Path("C:/Windows/Fonts/malgunbd.ttf" if bold else "C:/Windows/Fonts/malgun.ttf"),
            Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc" if bold else "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
            Path("/usr/share/fonts/opentype/noto/NotoSansCJKkr-Bold.otf" if bold else "/usr/share/fonts/opentype/noto/NotoSansCJKkr-Regular.otf"),
            Path("/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc" if bold else "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"),
            Path("/usr/share/fonts/truetype/noto/NotoSansKR-Bold.ttf" if bold else "/usr/share/fonts/truetype/noto/NotoSansKR-Regular.ttf"),
            Path("/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf" if bold else "/usr/share/fonts/truetype/nanum/NanumGothic.ttf"),
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        ]
        search_roots = [
            Path("/usr/share/fonts/opentype/noto"),
            Path("/usr/share/fonts/truetype/noto"),
            Path("/usr/share/fonts/truetype/nanum"),
        ]
        preferred_names = (
            ["*Bold*.ttc", "*Bold*.otf", "*Bold*.ttf"] if bold else ["*Regular*.ttc", "*Regular*.otf", "*Regular*.ttf", "*.ttc", "*.otf", "*.ttf"]
        )
        for root in search_roots:
            if root.exists():
                for pattern in preferred_names:
                    candidates.extend(root.glob(pattern))
        for path in candidates:
            if path.exists():
                try:
                    return ImageFont.truetype(str(path), size)
                except OSError:
                    continue
        return ImageFont.load_default()

    title_font = load_font(54, bold=True)
    body_font = load_font(34)
    small_font = load_font(22)

    image_paths: list[Path] = []
    for index, slide in enumerate(slides, 1):
        image_asset = get_slide_asset(asset_dir, index, IMAGE_ASSET_EXTS) if asset_dir else None
        if image_asset:
            image = Image.open(image_asset).convert("RGB")
            image = ImageOps.fit(image, (1280, 720), method=Image.Resampling.LANCZOS)
            overlay = Image.new("RGBA", (1280, 720), (5, 10, 24, 135))
            image = Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB")
        else:
            image = Image.new("RGB", (1280, 720), "#050A18")
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((54, 48, 1226, 116), radius=18, fill="#111827", outline="#22D3EE", width=2)
        draw.text((84, 67), slide["title"], font=small_font, fill="#E0F2FE")

        screen_lines = wrap_text_by_width(draw, slide["screen"], title_font, 1060)
        y = 170
        for line in screen_lines[:4]:
            draw.text((84, y), line, font=title_font, fill="#F8FAFC")
            y += 70

        draw.rounded_rectangle((84, 450, 1196, 642), radius=24, fill="#0F172A", outline="#334155", width=2)
        draw.text((116, 474), "내레이션", font=small_font, fill="#22D3EE")
        narration_lines = wrap_text_by_width(draw, slide["narration"], body_font, 1000)
        y = 512
        for line in narration_lines[:3]:
            draw.text((116, y), line, font=body_font, fill="#CBD5E1")
            y += 42

        draw.text((84, 676), "HYUNTOP NEWS", font=small_font, fill="#64748B")
        output_path = output_dir / f"slide_{index:02}.png"
        image.save(output_path)
        image_paths.append(output_path)
    return image_paths


def create_slide_images(slide_text: str, output_dir: Path, asset_dir: Path | None = None) -> list[Path]:
    from PIL import Image, ImageDraw, ImageFont, ImageOps

    slides = parse_slide_blocks_for_assets(slide_text)
    output_dir.mkdir(parents=True, exist_ok=True)

    def load_font(size: int, bold: bool = False):
        candidates = [
            Path("C:/Windows/Fonts/malgunbd.ttf" if bold else "C:/Windows/Fonts/malgun.ttf"),
            Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc" if bold else "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
            Path("/usr/share/fonts/opentype/noto/NotoSansCJKkr-Bold.otf" if bold else "/usr/share/fonts/opentype/noto/NotoSansCJKkr-Regular.otf"),
            Path("/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc" if bold else "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"),
            Path("/usr/share/fonts/truetype/noto/NotoSansKR-Bold.ttf" if bold else "/usr/share/fonts/truetype/noto/NotoSansKR-Regular.ttf"),
            Path("/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf" if bold else "/usr/share/fonts/truetype/nanum/NanumGothic.ttf"),
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        ]
        for path in candidates:
            if path.exists():
                try:
                    return ImageFont.truetype(str(path), size)
                except OSError:
                    continue
        return ImageFont.load_default()

    title_font = load_font(42, bold=True)
    headline_font = load_font(46, bold=True)
    body_font = load_font(27)
    small_font = load_font(20)

    image_paths: list[Path] = []
    for index, slide in enumerate(slides, 1):
        canvas = Image.new("RGB", (1280, 720), "#050A18")
        draw = ImageDraw.Draw(canvas)

        draw.rounded_rectangle((44, 34, 1236, 116), radius=18, fill="#111827", outline="#22D3EE", width=2)
        title = slide.get("title", f"슬라이드 {index}").strip() or f"슬라이드 {index}"
        title_lines = wrap_text_by_width(draw, title, title_font, 1110)
        y = 54
        for line in title_lines[:2]:
            draw.text((74, y), line, font=title_font, fill="#F8FAFC")
            y += 42

        image_box = (54, 150, 568, 592)
        image_asset = get_slide_asset(asset_dir, index, IMAGE_ASSET_EXTS) if asset_dir else None
        if image_asset:
            photo = Image.open(image_asset).convert("RGB")
            photo = ImageOps.fit(photo, (image_box[2] - image_box[0], image_box[3] - image_box[1]), method=Image.Resampling.LANCZOS)
            canvas.paste(photo, (image_box[0], image_box[1]))
            draw.rounded_rectangle(image_box, radius=22, outline="#334155", width=3)
        else:
            draw.rounded_rectangle(image_box, radius=22, fill="#0F172A", outline="#334155", width=3)
            draw.text((92, 326), "HYUNTOP NEWS", font=title_font, fill="#64748B")
            draw.text((92, 382), "이미지 영역", font=small_font, fill="#94A3B8")

        content_box = (610, 150, 1226, 430)
        draw.rounded_rectangle(content_box, radius=22, fill="#111827", outline="#334155", width=2)
        screen = slide.get("screen", "").strip() or title
        screen_lines = wrap_text_by_width(draw, screen, headline_font, 540)
        y = 184
        for line in screen_lines[:5]:
            draw.text((646, y), line, font=headline_font, fill="#F8FAFC")
            y += 58

        narration_box = (610, 460, 1226, 642)
        draw.rounded_rectangle(narration_box, radius=22, fill="#0F172A", outline="#334155", width=2)
        draw.text((646, 482), "내레이션", font=small_font, fill="#22D3EE")
        narration = slide.get("narration", "").strip()
        narration_lines = wrap_text_by_width(draw, narration, body_font, 540)
        y = 518
        for line in narration_lines[:4]:
            draw.text((646, y), line, font=body_font, fill="#CBD5E1")
            y += 36

        draw.text((54, 676), "HYUNTOP NEWS", font=small_font, fill="#64748B")
        output_path = output_dir / f"slide_{index:02}.png"
        canvas.save(output_path)
        image_paths.append(output_path)
    return image_paths


def parse_slide_blocks_for_assets(slide_text: str) -> list[dict[str, str]]:
    slides: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    mode = ""

    for line in slide_text.splitlines():
        clean = line.strip().lstrip("-").strip()
        if not clean:
            continue

        if clean.startswith("##") and ("슬라이드" in clean or "Slide" in clean):
            if current:
                slides.append(current)
            current = {"title": clean.lstrip("# ").strip(), "screen": "", "narration": ""}
            mode = ""
            continue

        if current is None:
            continue

        label = clean.rstrip(":：").strip()
        if label in {"화면 문구", "화면", "screen", "Screen"}:
            mode = "screen"
            continue
        if label in {"내레이션", "나레이션", "narration", "Narration"}:
            mode = "narration"
            continue
        if clean.startswith(("화면 문구:", "화면:", "screen:", "Screen:")):
            mode = "screen"
            current["screen"] = (current["screen"] + "\n" + clean.split(":", 1)[1].strip()).strip()
            continue
        if clean.startswith(("내레이션:", "나레이션:", "narration:", "Narration:")):
            mode = "narration"
            current["narration"] = (current["narration"] + "\n" + clean.split(":", 1)[1].strip()).strip()
            continue

        if mode == "screen":
            current["screen"] = (current["screen"] + "\n" + clean).strip()
        elif mode == "narration":
            current["narration"] = (current["narration"] + "\n" + clean).strip()
        elif not current["screen"]:
            current["screen"] = clean

    if current:
        slides.append(current)
    return slides[:6]


def clean_video_narration(text: str) -> str:
    replacements = {
        "주요내용": "내용",
        "주요 내용": "내용",
        "핵심메시지": "핵심",
        "핵심 메시지": "핵심",
        "핵심메세지": "핵심",
        "핵심 메세지": "핵심",
    }
    cleaned = text or ""
    for old, new in replacements.items():
        cleaned = cleaned.replace(old, new)
    return cleaned


def clear_rendered_video_outputs(package_dir: Path) -> None:
    video_dir = package_dir / "video_package"
    for folder_name in ("audio", "clips"):
        shutil.rmtree(video_dir / folder_name, ignore_errors=True)
    for file_name in ("final-video.mp4", "concat-list.txt"):
        file_path = video_dir / file_name
        if file_path.exists():
            file_path.unlink()


def download_pexels_image(query: str, output_path: Path, api_key: str, page: int = 1) -> None:
    params = urllib.parse.urlencode(
        {
            "query": query,
            "per_page": 1,
            "page": max(1, page),
            "orientation": "landscape",
            "locale": "ko-KR",
        }
    )
    request = urllib.request.Request(
        f"https://api.pexels.com/v1/search?{params}",
        headers={"Authorization": api_key, "User-Agent": "HYUNTOP-News-Dashboard/1.0"},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        payload = json.loads(response.read().decode("utf-8"))

    photos = payload.get("photos") or []
    if not photos and page > 1:
        return download_pexels_image(query, output_path, api_key, page=1)
    if not photos:
        raise RuntimeError(f"'{query}' 검색 결과가 없습니다.")

    src = photos[0].get("src", {})
    image_url = src.get("landscape") or src.get("large") or src.get("original")
    if not image_url:
        raise RuntimeError("이미지 주소를 찾지 못했습니다.")

    image_request = urllib.request.Request(image_url, headers={"User-Agent": "HYUNTOP-News-Dashboard/1.0"})
    with urllib.request.urlopen(image_request, timeout=30) as response:
        output_path.write_bytes(response.read())


def pexels_query_candidates(slide: dict[str, str], title: str, index: int) -> list[str]:
    raw = " ".join([slide.get("title", ""), slide.get("screen", ""), title]).strip()
    first_query = guess_image_query({"title": raw, "screen": slide.get("screen", "")}, title)
    candidates = [first_query]

    lowered = raw.lower()
    if any(word in lowered for word in ("부동산", "주택", "아파트", "임대", "세제", "세금", "매매")):
        candidates += ["real estate korea", "apartment building", "housing market", "home finance"]
    elif any(word in lowered for word in ("경제", "금리", "환율", "주식", "투자", "시장")):
        candidates += ["financial market", "business news", "stock market", "economy"]
    elif any(word in lowered for word in ("ai", "인공지능", "기술", "반도체", "데이터")):
        candidates += ["artificial intelligence", "technology", "data center", "semiconductor"]
    else:
        candidates += ["news", "business meeting", "city skyline", "newspaper"]

    candidates.append("korea city")

    unique: list[str] = []
    for query in candidates:
        query = re.sub(r"\s+", " ", query).strip()
        if query and query not in unique:
            unique.append(query)
    return unique


def auto_fill_slide_images(package_dir: Path) -> list[str]:
    api_key = get_config_value("PEXELS_API_KEY")
    if not api_key:
        raise RuntimeError("PEXELS_API_KEY가 없습니다. 로컬 .env 또는 Streamlit Secrets에 추가해 주세요.")

    slide_text = read_text_if_exists(package_dir / "04-youtube-slides.md")
    slides = parse_slide_blocks_for_assets(slide_text)
    if not slides:
        raise RuntimeError("유튜브 슬라이드 대본이 없어 자동 이미지를 찾을 수 없습니다.")

    asset_dir = get_slide_asset_dir(package_dir)
    asset_dir.mkdir(parents=True, exist_ok=True)
    title = extract_markdown_title(read_text_if_exists(package_dir / "01-blog-post.md"), package_dir.name)

    results: list[str] = []
    for index, slide in enumerate(slides[:6], 1):
        if get_slide_asset(asset_dir, index, VIDEO_ASSET_EXTS):
            results.append(f"슬라이드 {index}: 기존 영상 파일 유지")
            continue
        output_path = asset_dir / f"slide_{index:02}.jpg"
        used_query = ""
        for query in pexels_query_candidates(slide, title, index):
            try:
                download_pexels_image(query, output_path, api_key, page=index)
                used_query = query
                break
            except Exception:
                continue
        if used_query:
            results.append(f"슬라이드 {index}: 자동 이미지 추가({used_query})")
        else:
            results.append(f"슬라이드 {index}: 이미지 검색 결과 없음, 기본 배경 사용")
    return results


def create_slide_images(slide_text: str, output_dir: Path, asset_dir: Path | None = None) -> list[Path]:
    from PIL import Image, ImageDraw, ImageFont, ImageOps

    slides = parse_slide_blocks_for_assets(slide_text)
    output_dir.mkdir(parents=True, exist_ok=True)

    def load_font(size: int, bold: bool = False):
        candidates = [
            Path("C:/Windows/Fonts/malgunbd.ttf" if bold else "C:/Windows/Fonts/malgun.ttf"),
            Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc" if bold else "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
            Path("/usr/share/fonts/truetype/noto/NotoSansKR-Bold.ttf" if bold else "/usr/share/fonts/truetype/noto/NotoSansKR-Regular.ttf"),
            Path("/usr/share/fonts/truetype/nanum/NanumGothicBold.ttf" if bold else "/usr/share/fonts/truetype/nanum/NanumGothic.ttf"),
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        ]
        for path in candidates:
            if path.exists():
                try:
                    return ImageFont.truetype(str(path), size)
                except OSError:
                    continue
        return ImageFont.load_default()

    title_font = load_font(38, bold=True)
    headline_font = load_font(44, bold=True)
    body_font = load_font(27)
    small_font = load_font(20)

    def fit_font(
        text: str,
        start_size: int,
        min_size: int,
        max_width: int,
        max_lines: int,
        bold: bool = True,
        max_height: int | None = None,
        line_gap: int = 6,
    ):
        for size in range(start_size, min_size - 1, -2):
            font = load_font(size, bold=bold)
            lines = wrap_text_by_width(draw, text, font, max_width)
            line_height = size + line_gap
            height_ok = max_height is None or len(lines[:max_lines]) * line_height <= max_height
            if len(lines) <= max_lines and height_ok:
                return font, lines
        font = load_font(min_size, bold=bold)
        return font, wrap_text_by_width(draw, text, font, max_width)[:max_lines]

    image_paths: list[Path] = []
    for index, slide in enumerate(slides, 1):
        canvas = Image.new("RGB", (1280, 720), "#050A18")
        draw = ImageDraw.Draw(canvas)

        raw_title = slide.get("title", f"슬라이드 {index}").strip() or f"슬라이드 {index}"
        screen = slide.get("screen", "").strip() or raw_title
        header_title = screen if index == 1 else raw_title
        if index == 1 and not header_title.startswith("슬라이드 1"):
            header_title = f"슬라이드 1. {header_title}"

        title_box = (44, 34, 1236, 142)
        draw.rounded_rectangle(title_box, radius=18, fill="#111827", outline="#22D3EE", width=2)
        fitted_title_font, title_lines = fit_font(header_title, 34, 20, 1110, 2, max_height=70, line_gap=4)
        title_line_height = fitted_title_font.size + 4 if hasattr(fitted_title_font, "size") else 30
        title_total_height = len(title_lines) * title_line_height
        y = title_box[1] + max(14, ((title_box[3] - title_box[1]) - title_total_height) // 2)
        for line in title_lines:
            draw.text((74, y), line, font=fitted_title_font, fill="#F8FAFC")
            y += title_line_height

        image_box = (54, 164, 568, 592)
        image_asset = get_slide_asset(asset_dir, index, IMAGE_ASSET_EXTS) if asset_dir else None
        if image_asset:
            photo = Image.open(image_asset).convert("RGB")
            photo = ImageOps.fit(photo, (image_box[2] - image_box[0], image_box[3] - image_box[1]), method=Image.Resampling.LANCZOS)
            canvas.paste(photo, (image_box[0], image_box[1]))
            draw.rounded_rectangle(image_box, radius=22, outline="#334155", width=3)
        else:
            draw.rounded_rectangle(image_box, radius=22, fill="#0F172A", outline="#334155", width=3)
            draw.text((92, 326), "HYUNTOP NEWS", font=title_font, fill="#64748B")
            draw.text((92, 382), "이미지 영역", font=small_font, fill="#94A3B8")

        content_box = (610, 164, 1226, 592)
        draw.rounded_rectangle(content_box, radius=22, fill="#111827", outline="#334155", width=2)
        fitted_headline_font, screen_lines = fit_font(screen, 44, 28, 540, 7)
        headline_line_height = fitted_headline_font.size + 12 if hasattr(fitted_headline_font, "size") else 44
        total_height = len(screen_lines) * headline_line_height
        y = max(180, 150 + ((592 - 150) - total_height) // 2)
        for line in screen_lines:
            draw.text((646, y), line, font=fitted_headline_font, fill="#F8FAFC")
            y += headline_line_height

        draw.text((54, 676), "HYUNTOP NEWS", font=small_font, fill="#64748B")
        output_path = output_dir / f"slide_{index:02}.png"
        canvas.save(output_path)
        image_paths.append(output_path)
    return image_paths


def create_video_package(package_dir: Path) -> Path:
    blog_text = read_text_if_exists(package_dir / "01-blog-post.md")
    slide_text = read_text_if_exists(package_dir / "04-youtube-slides.md")
    vrew_text = read_text_if_exists(package_dir / "05-vrew-script.txt")
    title = extract_markdown_title(blog_text, package_dir.name)
    short_title = title.replace("지금 놓치면 뒤늦게 알게 됩니다", "").strip(" -")
    package_name = safe_video_name(short_title)

    video_dir = package_dir / "video_package"
    video_dir.mkdir(parents=True, exist_ok=True)
    slide_image_dir = video_dir / "slide_images"
    asset_dir = get_slide_asset_dir(package_dir)
    create_slide_images(slide_text, slide_image_dir, asset_dir if asset_dir.exists() else None)

    files_to_copy = {
        "youtube-slides.pptx": package_dir / "06-youtube-slides.pptx",
        "youtube-slides.md": package_dir / "04-youtube-slides.md",
        "vrew-script.txt": package_dir / "05-vrew-script.txt",
        "blog-post.md": package_dir / "01-blog-post.md",
        "tistory-post.md": package_dir / "02-tistory-post.md",
    }
    for output_name, source in files_to_copy.items():
        if source.exists():
            shutil.copy2(source, video_dir / output_name)
    if asset_dir.exists():
        asset_copy_dir = video_dir / "uploaded-assets"
        if asset_copy_dir.exists():
            shutil.rmtree(asset_copy_dir)
        shutil.copytree(asset_dir, asset_copy_dir)

    slide_lines = parse_slide_headings(slide_text)
    chapters = "\n".join(f"{index - 1}:00 {line}" for index, line in enumerate(slide_lines, start=1))
    if not chapters:
        chapters = "0:00 시작\n0:30 주요 흐름\n1:00 마무리"

    upload_info = f"""# 유튜브 업로드 패키지

## 제목 후보
1. {short_title}
2. 지금 놓치면 늦는 뉴스: {short_title}
3. 숫자보다 중요한 오늘의 흐름
4. 뉴스가 말해주는 다음 변화
5. 지금 봐야 할 시장의 신호

## 설명란 초안
오늘 영상에서는 아래 뉴스를 바탕으로 지금 확인해야 할 흐름을 정리합니다.

{short_title}

본문과 관련 정보를 함께 확인하면서 이 이슈가 왜 중요한지, 앞으로 무엇을 봐야 하는지 살펴봅니다.

## 챕터
{chapters}

## 해시태그
#뉴스정리 #경제뉴스 #오늘의뉴스 #이슈분석 #유튜브쇼츠 #시사뉴스 #현탑뉴스"""

    thumbnail_text = f"""# 썸네일 문구 후보

1. 지금 놓치면 늦습니다
2. 이 뉴스가 중요한 이유
3. 조용히 바뀌는 흐름
4. 숫자보다 중요한 변화
5. 앞으로 더 중요해질 이슈
6. {short_title[:22]}
"""

    checklist = """# 영상 제작 체크리스트
## Vrew 작업
- vrew-script.txt 열기
- Vrew에 대본 붙여넣기
- AI 음성 선택
- 자막 자동 생성 확인

## 슬라이드 작업
- youtube-slides.pptx 열기
- 슬라이드 1~6 확인
- 필요하면 이미지와 아이콘 추가
- Vrew 또는 편집앱에 슬라이드 삽입

## 업로드 전 확인
- 제목 후보 중 하나 선택
- 설명란 붙여넣기
- 해시태그 확인
- 썸네일 문구 선택
- 저작권 문제 없는 이미지/음원 사용
"""

    (video_dir / "upload-info.md").write_text(upload_info, encoding="utf-8")
    (video_dir / "thumbnail-copy.md").write_text(thumbnail_text, encoding="utf-8")
    (video_dir / "production-checklist.md").write_text(checklist, encoding="utf-8")

    zip_path = package_dir / f"{package_name}-video-package.zip"
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for file_path in video_dir.rglob("*"):
            if file_path.is_file():
                archive.write(file_path, file_path.relative_to(video_dir))
    return zip_path


def write_wave_file(output_path: Path, pcm: bytes, channels: int = 1, rate: int = 24000, sample_width: int = 2) -> None:
    with wave.open(str(output_path), "wb") as wave_file:
        wave_file.setnchannels(channels)
        wave_file.setsampwidth(sample_width)
        wave_file.setframerate(rate)
        wave_file.writeframes(pcm)


def create_google_tts_audio(text: str, output_path: Path) -> bool:
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        return False

    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=api_key)
        model = os.getenv("GEMINI_TTS_MODEL", "gemini-3.1-flash-tts-preview").strip()
        voice_name = os.getenv("GEMINI_TTS_VOICE", "Kore").strip()
        prompt = (
            "다음 한국어 뉴스 영상 내레이션을 차분하고 신뢰감 있는 목소리로 읽어줘. "
            "문장 사이에는 자연스럽게 짧은 쉼을 넣어줘.\n\n"
            f"{text}"
        )
        response = client.models.generate_content(
            model=model,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_modalities=["AUDIO"],
                speech_config=types.SpeechConfig(
                    voice_config=types.VoiceConfig(
                        prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice_name)
                    )
                ),
            ),
        )
        audio_data = response.candidates[0].content.parts[0].inline_data.data
        if isinstance(audio_data, str):
            import base64

            audio_data = base64.b64decode(audio_data)
        write_wave_file(output_path, audio_data)
        return True
    except Exception:
        return False


async def create_tts_audio(text: str, output_path: Path, voice: str = "ko-KR-SunHiNeural") -> None:
    if create_google_tts_audio(text, output_path):
        return

    text_path = output_path.with_suffix(".tts.txt")
    script_path = output_path.with_suffix(".tts.ps1")
    text_path.write_text(text, encoding="utf-8")
    script_path.write_text(
        """
param(
    [string]$TextPath,
    [string]$OutputPath
)
Add-Type -AssemblyName System.Speech
$text = Get-Content -LiteralPath $TextPath -Raw -Encoding UTF8
$speaker = New-Object System.Speech.Synthesis.SpeechSynthesizer
$speaker.Rate = 0
$speaker.Volume = 100
$speaker.SetOutputToWaveFile($OutputPath)
$speaker.Speak($text)
$speaker.Dispose()
""".strip(),
        encoding="utf-8",
    )
    try:
        result = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script_path),
                str(text_path),
                str(output_path),
            ],
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=120,
        )
        if result.returncode != 0:
            message = result.stderr.strip() or result.stdout.strip() or "음성 파일을 만들지 못했습니다."
            raise RuntimeError(message[-1200:])
    finally:
        text_path.unlink(missing_ok=True)
        script_path.unlink(missing_ok=True)


def run_command(command: list[str]) -> None:
    result = subprocess.run(command, text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=300)
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or "영상 생성 명령이 실패했습니다."
        raise RuntimeError(message[-1200:])


def get_ffmpeg_path() -> str:
    simple_ffmpeg = BASE_DIR / "ffmpeg_tools" / "ffmpeg.exe"
    if simple_ffmpeg.exists():
        return str(simple_ffmpeg)

    for vendor_dir in (VIDEO_VENDOR_DIR, VENDOR_DIR):
        binaries_dir = vendor_dir / "imageio_ffmpeg" / "binaries"
        if binaries_dir.exists():
            for path in binaries_dir.glob("ffmpeg*.exe"):
                if path.exists():
                    return str(path)

    try:
        from imageio_ffmpeg import get_ffmpeg_exe

        ffmpeg_path = get_ffmpeg_exe()
        if Path(ffmpeg_path).exists():
            return ffmpeg_path
    except Exception:
        pass

    ffmpeg_path = shutil.which("ffmpeg")
    if ffmpeg_path:
        return ffmpeg_path

    checked_paths = [
        str(VIDEO_VENDOR_DIR / "imageio_ffmpeg" / "binaries"),
        str(VENDOR_DIR / "imageio_ffmpeg" / "binaries"),
    ]
    raise RuntimeError(
        "ffmpeg 실행 파일을 찾지 못했습니다. 현재 실행 폴더: "
        f"{BASE_DIR} / 확인한 위치: {' | '.join(checked_paths)}"
    )


def create_mp4_video(package_dir: Path, progress_callback=None) -> Path:
    def report(step: int, total: int, message: str) -> None:
        if progress_callback:
            progress_callback(step, total, message)

    report(1, 6, "영상 재료를 확인하는 중입니다.")
    slide_text = read_text_if_exists(package_dir / "04-youtube-slides.md")
    slides = parse_slide_blocks_for_assets(slide_text)
    if not slides:
        raise RuntimeError("유튜브 슬라이드 대본을 찾지 못했습니다. 먼저 콘텐츠 패키지를 생성해 주세요.")

    report(2, 6, "영상 제작 패키지를 준비하는 중입니다.")
    vrew_text = read_text_if_exists(package_dir / "05-vrew-script.txt")
    vrew_slides = parse_slide_blocks_for_assets(vrew_text)

    create_video_package(package_dir)
    video_dir = package_dir / "video_package"
    slide_image_dir = video_dir / "slide_images"
    asset_dir = get_slide_asset_dir(package_dir)
    audio_dir = video_dir / "audio"
    clip_dir = video_dir / "clips"
    if audio_dir.exists():
        shutil.rmtree(audio_dir)
    if clip_dir.exists():
        shutil.rmtree(clip_dir)
    audio_dir.mkdir(parents=True, exist_ok=True)
    clip_dir.mkdir(parents=True, exist_ok=True)

    ffmpeg = get_ffmpeg_path()
    clip_paths: list[Path] = []
    total_steps = max(len(slides) + 4, 6)

    for index, slide in enumerate(slides, 1):
        report(index + 2, total_steps, f"{index}번 슬라이드 음성과 영상 조각을 만드는 중입니다.")
        image_path = slide_image_dir / f"slide_{index:02}.png"
        video_asset = get_slide_asset(asset_dir, index, VIDEO_ASSET_EXTS) if asset_dir.exists() else None
        audio_path = audio_dir / f"slide_{index:02}.wav"
        clip_path = clip_dir / f"clip_{index:02}.mp4"
        narration = clean_video_narration(slide.get("narration", "").strip() or slide.get("screen", "").strip() or "다음 내용을 확인해 보겠습니다.")

        narration_slide = vrew_slides[index - 1] if index <= len(vrew_slides) else slide
        narration = clean_video_narration(
            narration_slide.get("narration", "").strip()
            or slide.get("narration", "").strip()
            or slide.get("screen", "").strip()
            or narration
        )
        asyncio.run(create_tts_audio(narration, audio_path))

        if video_asset:
            run_command(
                [
                    ffmpeg,
                    "-y",
                    "-stream_loop",
                    "-1",
                    "-i",
                    str(video_asset),
                    "-i",
                    str(audio_path),
                    "-vf",
                    "scale=1280:720:force_original_aspect_ratio=increase,crop=1280:720",
                    "-c:v",
                    "libx264",
                    "-c:a",
                    "aac",
                    "-b:a",
                    "192k",
                    "-pix_fmt",
                    "yuv420p",
                    "-shortest",
                    str(clip_path),
                ]
            )
        else:
            run_command(
                [
                    ffmpeg,
                    "-y",
                    "-loop",
                    "1",
                    "-i",
                    str(image_path),
                    "-i",
                    str(audio_path),
                    "-c:v",
                    "libx264",
                    "-tune",
                    "stillimage",
                    "-c:a",
                    "aac",
                    "-b:a",
                    "192k",
                    "-pix_fmt",
                    "yuv420p",
                    "-shortest",
                    str(clip_path),
                ]
            )
        clip_paths.append(clip_path)

    report(total_steps - 1, total_steps, "영상 조각을 하나의 MP4로 합치는 중입니다.")
    concat_path = video_dir / "concat-list.txt"
    concat_path.write_text(
        "\n".join(f"file '{clip_path.as_posix()}'" for clip_path in clip_paths),
        encoding="utf-8",
    )
    final_video = video_dir / "final-video.mp4"
    run_command(
        [
            ffmpeg,
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_path),
            "-c",
            "copy",
            str(final_video),
        ]
    )
    report(total_steps, total_steps, "MP4 영상 생성이 완료되었습니다.")
    return final_video


def show_content_viewer(settings: dict) -> None:
    package_dir = get_recent_content_package(settings)
    if package_dir is None:
        st.info("아직 저장된 콘텐츠 패키지가 없습니다. '지금 테스트 실행'을 누르면 생성됩니다.")
        return

    st.markdown(
        f"""
        <div class="side-note">
            <strong>최근 저장 콘텐츠</strong>
            {html.escape(package_dir.name)}
        </div>
        """,
        unsafe_allow_html=True,
    )

    content_menu = {
        "블로그 글": "01-blog-post.md",
        "티스토리 글": "02-tistory-post.md",
        "쓰레드": "03-thread-post.txt",
        "유튜브 대본": "04-youtube-slides.md",
        "PPTX": "06-youtube-slides.pptx",
        "Vrew 대본": "05-vrew-script.txt",
        "영상 제작": "",
    }
    selected_content = st.session_state.get("content_view", "블로그 글")
    if selected_content not in content_menu:
        selected_content = "블로그 글"

    editable_labels = {"블로그 글", "티스토리 글", "쓰레드", "유튜브 대본", "Vrew 대본"}

    if selected_content in editable_labels:
        file_path = package_dir / content_menu[selected_content]
        current_text = read_text_if_exists(file_path)
        if selected_content != "블로그 글" and looks_broken_text(current_text):
            st.warning("이 저장 콘텐츠는 예전에 깨진 상태로 만들어졌습니다. 블로그 글 기준으로 다시 만들면 복구됩니다.")
            if st.button("깨진 글 복구하기", use_container_width=True, key=f"repair_{selected_content}"):
                rebuild_derivative_files_from_blog(package_dir)
                st.session_state.pop("video_package_zip", None)
                st.session_state.pop("mp4_video_path", None)
                st.success("티스토리, 쓰레드, 유튜브, Vrew 콘텐츠를 다시 만들었습니다.")
                st.rerun()
        editor_height = 180 if selected_content == "쓰레드" else 560
        file_version = int(file_path.stat().st_mtime) if file_path.exists() else 0
        edited_text = st.text_area(
            selected_content,
            value=current_text,
            height=editor_height,
            label_visibility="collapsed",
            key=f"editor_{package_dir.name}_{selected_content}_{file_version}",
        )
        col_save, col_hint = st.columns([1, 2])
        with col_save:
            if st.button("수정 내용 저장", use_container_width=True, key=f"save_{selected_content}"):
                file_path.write_text(edited_text, encoding="utf-8")
                if content_menu[selected_content] in {"04-youtube-slides.md", "05-vrew-script.txt"}:
                    clear_rendered_video_outputs(package_dir)
                    st.session_state.pop("mp4_video_path", None)
                    st.session_state.pop("video_package_zip", None)
                st.success("저장했습니다. 영상 대본을 수정했다면 MP4를 다시 만들면 반영됩니다.")
        with col_hint:
            if selected_content in {"유튜브 대본", "Vrew 대본"}:
                st.info("대본을 고친 뒤에는 영상 제작 패키지와 MP4를 다시 만들어야 반영됩니다.")
            else:
                st.info("여기서 바로 고치고 저장할 수 있습니다.")
        if selected_content == "블로그 글":
            st.divider()
            st.caption("블로그 글을 보강한 뒤 아래 버튼을 누르면 티스토리, 쓰레드, 유튜브, Vrew, PPTX를 다시 만듭니다.")
            if st.button("블로그 글 기준으로 전체 다시 만들기", use_container_width=True):
                file_path.write_text(edited_text, encoding="utf-8")
                rebuild_derivative_files_from_blog(package_dir)
                st.session_state.pop("video_package_zip", None)
                st.session_state.pop("mp4_video_path", None)
                st.success("블로그 글 기준으로 나머지 콘텐츠를 다시 만들었습니다. 영상은 MP4를 다시 만들면 됩니다.")
    elif selected_content == "PPTX":
        pptx_path = package_dir / content_menu[selected_content]
        if pptx_path.exists():
            st.download_button(
                "PPTX 슬라이드 다운로드",
                data=pptx_path.read_bytes(),
                file_name=pptx_path.name,
                mime="application/vnd.openxmlformats-officedocument.presentationml.presentation",
                use_container_width=True,
            )
            st.caption("유튜브 슬라이드 1~6 순서대로 PPTX가 생성됩니다.")
        else:
            st.info("아직 PPTX 파일이 없습니다. 지금 테스트 실행을 누르면 생성됩니다.")
    elif selected_content == "영상 제작":
        st.markdown("### 영상 제작 패키지")
        st.caption("PPTX, Vrew 대본, 업로드 정보, 썸네일 문구, 제작 체크리스트를 ZIP으로 묶습니다.")
        st.markdown("#### 슬라이드별 이미지/영상 넣기")
        st.caption("사진은 슬라이드 배경으로 쓰고, MP4 영상은 해당 슬라이드 구간 배경 영상으로 씁니다.")
        asset_dir = get_slide_asset_dir(package_dir)
        if st.button("슬라이드 이미지 자동 채우기", use_container_width=True):
            try:
                results = auto_fill_slide_images(package_dir)
                st.session_state.pop("video_package_zip", None)
                st.session_state.pop("mp4_video_path", None)
                st.success("슬라이드 이미지를 자동으로 채웠습니다.")
                st.write("\n".join(results))
            except Exception as exc:
                st.error(f"자동 이미지 채우기 중 오류가 발생했습니다: {exc}")

        upload_cols = st.columns(2)
        for index in range(1, 7):
            with upload_cols[(index - 1) % 2]:
                current_asset = next(asset_dir.glob(f"slide_{index:02}.*"), None) if asset_dir.exists() else None
                if current_asset:
                    st.caption(f"슬라이드 {index}: {current_asset.name} 사용 중")
                uploaded_asset = st.file_uploader(
                    f"슬라이드 {index} 이미지/영상",
                    type=["png", "jpg", "jpeg", "webp", "mp4", "mov", "m4v"],
                    key=f"slide_asset_{package_dir.name}_{index}",
                )
                if uploaded_asset is not None:
                    saved_asset = save_slide_asset(package_dir, index, uploaded_asset)
                    st.success(f"슬라이드 {index}에 {saved_asset.name}을 넣었습니다.")
                    st.session_state.pop("video_package_zip", None)
                    st.session_state.pop("mp4_video_path", None)

        if st.button("영상 제작 패키지 만들기", use_container_width=True):
            zip_path = create_video_package(package_dir)
            st.session_state["video_package_zip"] = str(zip_path)
            st.success("영상 제작 패키지를 만들었습니다.")

        zip_path_text = st.session_state.get("video_package_zip", "")
        zip_path = Path(zip_path_text) if zip_path_text else next(package_dir.glob("*-video-package.zip"), None)
        if zip_path and zip_path.exists():
            st.download_button(
                "영상 제작 패키지 ZIP 다운로드",
                data=zip_path.read_bytes(),
                file_name=zip_path.name,
                mime="application/zip",
                use_container_width=True,
            )
            st.info("ZIP 안의 upload-info.md, thumbnail-copy.md, production-checklist.md를 순서대로 확인하세요.")
        else:
            st.info("아직 영상 제작 패키지가 없습니다. 위 버튼을 눌러 생성하세요.")

        st.divider()
        st.markdown("### MP4 자동 생성")
        st.caption("슬라이드 이미지 6장과 Edge TTS 한국어 음성을 합쳐 final-video.mp4를 만듭니다.")
        if st.button("MP4 영상 만들기", use_container_width=True):
            progress_bar = st.progress(0)
            progress_text = st.empty()

            def update_mp4_progress(step: int, total: int, message: str) -> None:
                ratio = min(max(step / max(total, 1), 0), 1)
                progress_bar.progress(ratio)
                progress_text.info(f"{message} ({step}/{total})")

            try:
                mp4_path = create_mp4_video(package_dir, progress_callback=update_mp4_progress)
                st.session_state["mp4_video_path"] = str(mp4_path)
                progress_bar.progress(1.0)
                progress_text.success("MP4 영상 생성이 완료되었습니다.")
                st.success("MP4 영상을 만들었습니다.")
            except Exception as exc:
                progress_text.error("MP4 영상 생성이 중단되었습니다.")
                st.error(f"MP4 생성 중 오류가 발생했습니다: {exc}")

        mp4_path_text = st.session_state.get("mp4_video_path", "")
        mp4_path = Path(mp4_path_text) if mp4_path_text else package_dir / "video_package" / "final-video.mp4"
        if mp4_path.exists():
            st.download_button(
                "MP4 영상 다운로드",
                data=mp4_path.read_bytes(),
                file_name=mp4_path.name,
                mime="video/mp4",
                use_container_width=True,
            )

def show_result(result: subprocess.CompletedProcess[str]) -> None:
    if result.returncode == 0:
        st.success("완료되었습니다.")
        if result.stdout.strip():
            st.code(result.stdout.strip())
    else:
        st.error("실행 중 오류가 발생했습니다.")
        message = result.stderr.strip() or result.stdout.strip()
        if message:
            st.code(message)


def is_cloud() -> bool:
    return bool(os.getenv("STREAMLIT_SHARING_MODE"))


def run_news_now(settings: dict) -> None:
    try:
        append_activity("Run started from dashboard")
        result = run_mailer(settings=settings)
        append_activity(summarize_run_for_log(result))
        if result.get("sent", 0):
            st.success(f"{result['recipient']} 주소로 뉴스 {result['sent']}개를 보냈습니다.")
            content_package = result.get("content_package")
            if content_package:
                selected_text = f"{result.get('selected_index')}번째 뉴스"
                if result.get("auto_selected"):
                    selected_text += "를 본문 기준으로 자동 선택"
                st.success(f"{selected_text}로 블로그/티스토리/쓰레드/유튜브/PPTX/Vrew 콘텐츠 패키지를 만들었습니다.")
                if result.get("content_message"):
                    st.info(result["content_message"])
            elif result.get("content_message"):
                st.warning(result["content_message"])
        else:
            st.warning(result.get("message", "보낼 뉴스가 없습니다."))
    except Exception as exc:
        append_activity(summarize_exception_for_log(exc))
        st.error(f"실행 중 오류가 발생했습니다: {exc}")


def card(title: str, value: str, caption: str = "", accent: str = "cyan") -> None:
    accent_colors = {
        "cyan": "#22d3ee",
        "violet": "#8b5cf6",
        "green": "#22c55e",
        "amber": "#f59e0b",
        "rose": "#fb7185",
    }
    color = accent_colors.get(accent, accent_colors["cyan"])
    st.markdown(
        f"""
        <div class="metric-card" style="border-color:{color}55;">
            <div class="metric-label">{html.escape(title)}</div>
            <div class="metric-value">{html.escape(value)}</div>
            <div class="metric-caption">{html.escape(caption)}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def get_dashboard_password() -> str:
    for name in ("DASHBOARD_PASSWORD", "APP_PASSWORD"):
        value = os.getenv(name, "").strip()
        if value:
            return value
        try:
            value = str(st.secrets.get(name, "")).strip()
        except Exception:
            value = ""
        if value:
            return value
    return ""


def require_dashboard_login() -> None:
    if not is_cloud():
        return

    password = get_dashboard_password()
    if not password:
        st.error("관리자 비밀번호가 설정되지 않았습니다.")
        st.info("Streamlit Secrets 또는 로컬 .env에 DASHBOARD_PASSWORD를 추가해 주세요.")
        st.stop()

    if st.session_state.get("dashboard_authenticated"):
        return

    st.markdown(
        """
        <div style="max-width:420px;margin:14vh auto 0;padding:28px;border:1px solid #263248;border-radius:8px;background:#111827;">
            <h2 style="margin-top:0;color:white;">관리자 로그인</h2>
            <p style="color:#8ea0bd;margin-bottom:18px;">비밀번호를 입력해야 뉴스 자동화 대시보드를 사용할 수 있습니다.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )
    typed_password = st.text_input("비밀번호", type="password", label_visibility="collapsed")
    if st.button("로그인", use_container_width=True):
        if hmac.compare_digest(typed_password, password):
            st.session_state["dashboard_authenticated"] = True
            st.rerun()
        else:
            st.error("비밀번호가 맞지 않습니다.")
    st.stop()


st.set_page_config(page_title="현탑부동산 뉴스 자동화", page_icon="HN", layout="wide")

st.markdown(
    """
    <style>
    :root {
        --bg: #070b16;
        --panel: #111827;
        --panel-2: #172033;
        --line: #263248;
        --text: #f8fafc;
        --muted: #8ea0bd;
        --cyan: #22d3ee;
        --violet: #8b5cf6;
        --green: #22c55e;
        --amber: #f59e0b;
    }

    .stApp {
        background: var(--bg);
        color: var(--text);
    }

    header[data-testid="stHeader"] {
        display: none;
    }

    div[data-testid="stToolbar"] {
        display: none;
    }

    div[data-testid="stDecoration"] {
        display: none;
    }

    section[data-testid="stSidebar"] {
        background: #0d1322;
        border-right: 1px solid var(--line);
    }

    section[data-testid="stSidebar"] * {
        color: var(--text);
    }

    div[data-testid="stSidebarNav"] {
        display: none;
    }

    .block-container {
        padding-top: 1.6rem;
        padding-bottom: 2rem;
        max-width: 1380px;
    }

    .topbar {
        display: flex;
        justify-content: space-between;
        align-items: flex-start;
        border-bottom: 1px solid var(--line);
        padding: 8px 0 18px;
        margin-bottom: 24px;
        min-height: 88px;
    }

    .brand {
        font-weight: 900;
        letter-spacing: .08em;
        color: white;
        font-size: 20px;
    }

    .status-row {
        display: flex;
        gap: 10px;
        align-items: center;
        color: var(--muted);
        font-size: 13px;
    }

    .status-pill {
        border: 1px solid #14532d;
        background: #052e1a;
        color: #4ade80;
        border-radius: 999px;
        padding: 4px 10px;
        font-weight: 700;
    }

    .page-title {
        font-size: 28px;
        line-height: 1.25;
        font-weight: 900;
        margin: 0 0 6px 0;
        color: white;
        word-break: keep-all;
    }

    .page-subtitle {
        color: var(--muted);
        margin-top: 6px;
        margin-bottom: 20px;
        font-size: 14px;
    }

    .section-title {
        color: #c4b5fd;
        font-size: 14px;
        font-weight: 800;
        letter-spacing: .06em;
        margin: 18px 0 10px;
        text-transform: uppercase;
    }

    .metric-card {
        min-height: 150px;
        background: linear-gradient(180deg, #182235 0%, #111827 100%);
        border: 1px solid var(--line);
        border-radius: 8px;
        padding: 18px;
        box-shadow: 0 14px 40px rgba(0, 0, 0, .25);
    }

    .metric-label {
        color: #dbeafe;
        font-size: 15px;
        font-weight: 800;
        margin-bottom: 10px;
    }

    .metric-value {
        color: white;
        font-size: 30px;
        line-height: 1.05;
        font-weight: 900;
    }

    .metric-caption {
        color: var(--muted);
        font-size: 13px;
        margin-top: 12px;
        word-break: break-word;
    }

    .log-box {
        background: #0b1020;
        border: 1px solid var(--line);
        border-radius: 8px;
        padding: 14px;
        color: #b7c5dc;
        font-family: Consolas, monospace;
        font-size: 12px;
        white-space: pre-wrap;
        min-height: 260px;
    }

    .side-note {
        background: #111827;
        border: 1px solid var(--line);
        border-radius: 8px;
        padding: 14px;
        margin-top: 12px;
    }

    .side-note strong {
        display: block;
        color: white;
        margin-bottom: 6px;
    }

    .sidebar-subtitle {
        color: var(--muted);
        font-size: 12px;
        font-weight: 800;
        margin: 18px 0 8px;
        text-transform: uppercase;
    }

    .stButton > button {
        border-radius: 8px;
        border: 1px solid #3b82f6;
        background: #172554;
        color: white;
        font-weight: 800;
        min-height: 42px;
    }

    .stButton > button:hover {
        border-color: var(--cyan);
        color: white;
        background: #1e3a8a;
    }

    div[data-testid="stForm"] {
        border: 1px solid var(--line);
        border-radius: 8px;
        padding: 20px;
        background: #0d1322;
    }

    input, textarea, select {
        border-radius: 8px !important;
    }

    .stTabs [data-baseweb="tab-list"] {
        gap: 8px;
    }

    .stTabs [data-baseweb="tab"] {
        background: #111827;
        border: 1px solid var(--line);
        border-radius: 8px;
        color: var(--text);
        padding: 10px 16px;
    }

    div[role="radiogroup"] label {
        background: #111827;
        border: 1px solid var(--line);
        border-radius: 8px;
        padding: 10px 12px;
        margin-bottom: 8px;
    }

    </style>
    """,
    unsafe_allow_html=True,
)

require_dashboard_login()

settings = load_settings()
now_text = datetime.now().strftime("%Y. %m. %d. %H:%M")

with st.sidebar:
    st.markdown('<div class="brand">HYUNTOP NEWS</div>', unsafe_allow_html=True)
    st.caption("자동화 컨트롤")
    menu = st.radio("메뉴", ["대시보드", "설정", "실행 기록"], label_visibility="collapsed")
    if menu == "대시보드":
        st.markdown('<div class="sidebar-subtitle">저장 콘텐츠</div>', unsafe_allow_html=True)
        st.radio(
            "저장 콘텐츠",
            ["블로그 글", "티스토리 글", "쓰레드", "유튜브 대본", "PPTX", "Vrew 대본", "영상 제작"],
            key="content_view",
            label_visibility="collapsed",
        )
    st.markdown(
        f"""
        <div class="side-note">
            <strong>시스템 상태</strong>
            데이터 수집: ONLINE<br>
            메일 발송: ONLINE<br>
            콘텐츠 패키지: {'ON' if settings['blog_enabled'] else 'OFF'}
        </div>
        <div class="side-note">
            <strong>오늘 설정</strong>
            키워드: {html.escape(str(settings['news_query'] or '전체'))}<br>
            시간: {html.escape(str(settings['schedule_time']))}<br>
            뉴스: {int(settings['news_limit'])}개
        </div>
        """,
        unsafe_allow_html=True,
    )

st.markdown(
    f"""
    <div class="topbar">
        <div>
            <div class="page-title">현탑부동산 뉴스 자동화 대시보드</div>
            <div class="page-subtitle">{now_text} KST · 메일 발송, 콘텐츠 패키지, 스케줄러 관리</div>
        </div>
        <div class="status-row">
            <span class="status-pill">LIVE</span>
            <span>settings.json 저장</span>
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)

if menu == "대시보드":
    st.markdown('<div class="section-title">Automation Overview</div>', unsafe_allow_html=True)
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        card("뉴스 키워드", str(settings["news_query"] or "전체"), "Google 뉴스 RSS 기준", "cyan")
    with col2:
        card("발송 시간", str(settings["schedule_time"]), "작업 스케줄러 등록 시간", "violet")
    with col3:
        recipient = str(settings["recipient_email"] or "기본 Gmail")
        card("수신 채널", recipient, "이메일 발송", "green")
    with col4:
        blog_status = "ON" if settings["blog_enabled"] else "OFF"
        pick_label = "본문 많은 뉴스 자동 선택" if int(settings.get("blog_pick_index", 0)) == 0 else f"{settings['blog_pick_index']}번째 뉴스로 생성"
        card("콘텐츠 패키지", blog_status, pick_label, "amber")

    st.markdown('<div class="section-title">Quick Actions</div>', unsafe_allow_html=True)
    action1, action2, action3 = st.columns([1, 1, 2])
    with action1:
        if st.button("지금 테스트 실행", use_container_width=True):
            run_news_now(settings)
    with action2:
        if st.button("스케줄러 등록/갱신", use_container_width=True, disabled=is_cloud()):
            show_result(run_powershell(["-File", str(SCHEDULE_SCRIPT), "-Time", settings["schedule_time"]]))
    with action3:
        if is_cloud():
            st.info("클라우드 예약 발송은 GitHub Actions가 담당합니다.")
        else:
            st.info("테스트 실행은 실제 메일을 발송합니다.")

    st.markdown('<div class="section-title">Recent Activity</div>', unsafe_allow_html=True)
    left, right = st.columns([1.4, 1])
    with left:
        st.markdown(
            f'<div class="log-box">{html.escape(chr(10).join(read_recent_logs()))}</div>',
            unsafe_allow_html=True,
        )
    with right:
        card("최근 콘텐츠 패키지", get_recent_draft(settings), "blog_drafts 폴더 기준", "rose")
        card("안정장치", f"재시도 {settings['retry_count']}회", f"시간초과 {settings['request_timeout_seconds']}초", "cyan")

    st.markdown('<div class="section-title">Saved Content</div>', unsafe_allow_html=True)
    show_content_viewer(settings)
elif menu == "설정":
    st.markdown('<div class="section-title">Configuration</div>', unsafe_allow_html=True)
    with st.form("settings_form"):
        tab_news, tab_blog, tab_guard = st.tabs(["뉴스/수신", "콘텐츠 제작", "안정장치"])

        with tab_news:
            news_query = st.text_input("키워드", value=settings["news_query"], placeholder="예: 과학, 경제, AI")
            news_limit = st.number_input("뉴스 개수", min_value=1, max_value=20, value=int(settings["news_limit"]))
            recipient_email = st.text_input("받는 이메일", value=settings["recipient_email"])
            notification_channel = st.selectbox(
                "수신 채널",
                options=["email", "none"],
                index=0 if settings["notification_channel"] == "email" else 1,
                format_func=lambda value: "이메일" if value == "email" else "알림 끄기",
            )
            schedule_time = st.text_input("매일 실행 시간", value=settings["schedule_time"], help="24시간 형식으로 입력하세요. 예: 07:00")

        with tab_blog:
            blog_enabled = st.toggle("콘텐츠 패키지 생성", value=bool(settings["blog_enabled"]))
            auto_pick = st.toggle(
                "본문이 충분한 뉴스 자동 선택",
                value=int(settings.get("blog_pick_index", 0)) == 0,
                disabled=not blog_enabled,
            )
            blog_pick_index = st.number_input(
                "고를 뉴스 번호",
                min_value=1,
                max_value=20,
                value=max(int(settings.get("blog_pick_index", 1)), 1),
                disabled=(not blog_enabled) or auto_pick,
            )
            blog_draft_dir = st.text_input("콘텐츠 저장 폴더", value=settings["blog_draft_dir"], disabled=not blog_enabled)
            content_candidate_limit = st.number_input(
                "자동 선택 후보 뉴스 수",
                min_value=5,
                max_value=30,
                value=int(settings.get("content_candidate_limit", 10)),
                disabled=(not blog_enabled) or (not auto_pick),
            )
            st.caption("자동 선택을 켜면 뉴스 목록 중 본문이나 요약이 가장 많은 기사를 콘텐츠 제작 기준으로 고릅니다.")
            low_cost_mode = st.toggle(
                "Gemini 비용 절약 모드",
                value=bool(settings.get("low_cost_mode", False)),
                disabled=not blog_enabled,
            )

        with tab_guard:
            retry_count = st.number_input("실패 시 재시도 횟수", min_value=0, max_value=10, value=int(settings["retry_count"]))
            retry_delay_seconds = st.number_input("재시도 간격(초)", min_value=1, max_value=60, value=int(settings["retry_delay_seconds"]))
            request_timeout_seconds = st.number_input("요청 시간 초과(초)", min_value=3, max_value=120, value=int(settings["request_timeout_seconds"]))
            error_email_enabled = st.toggle("오류 발생 시 이메일 알림", value=bool(settings["error_email_enabled"]))

        saved = st.form_submit_button("설정 저장", use_container_width=True)

    if saved:
        new_settings = {
            "news_query": news_query.strip(),
            "news_limit": int(news_limit),
            "recipient_email": recipient_email.strip(),
            "notification_channel": notification_channel,
            "schedule_time": schedule_time.strip(),
            "blog_enabled": bool(blog_enabled),
            "blog_pick_index": 0 if auto_pick else int(blog_pick_index),
            "blog_draft_dir": blog_draft_dir.strip() or "blog_drafts",
            "content_candidate_limit": int(content_candidate_limit),
            "low_cost_mode": bool(low_cost_mode),
            "retry_count": int(retry_count),
            "retry_delay_seconds": int(retry_delay_seconds),
            "request_timeout_seconds": int(request_timeout_seconds),
            "error_email_enabled": bool(error_email_enabled),
        }
        save_settings(new_settings)
        st.success("설정을 저장했습니다.")
        settings = new_settings

elif menu == "실행 기록":
    st.markdown('<div class="section-title">Execution Log</div>', unsafe_allow_html=True)
    st.markdown(
        f'<div class="log-box">{html.escape(chr(10).join(read_recent_logs(30)))}</div>',
        unsafe_allow_html=True,
    )
    with st.expander("현재 JSON 설정 보기"):
        st.json(settings)
