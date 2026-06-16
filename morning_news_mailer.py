import argparse
import ast
import html
import json
import os
import re
import smtplib
import ssl
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from email.message import EmailMessage
from html.parser import HTMLParser
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
SETTINGS_PATH = BASE_DIR / "settings.json"
DEFAULT_FEED_URL = "https://news.google.com/rss?hl=ko&gl=KR&ceid=KR:ko"


@dataclass
class NewsItem:
    title: str
    link: str
    source: str
    summary: str = ""
    article_text: str = ""


@dataclass
class ContentPackage:
    news_title: str
    blog_post: str
    tistory_post: str
    thread_post: str
    slide_script: str
    vrew_script: str
    directory: str = ""
    pptx_path: str = ""


def load_env_file(path: Path = BASE_DIR / ".env") -> None:
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def load_settings(path: Path = SETTINGS_PATH) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8-sig"))


def get_secret(name: str) -> str:
    value = os.getenv(name, "").strip()
    if value:
        return value

    try:
        import streamlit as st

        return str(st.secrets.get(name, "")).strip()
    except Exception:
        return ""


class ArticleTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.skip_depth = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript", "svg", "header", "footer", "nav"}:
            self.skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "svg", "header", "footer", "nav"} and self.skip_depth:
            self.skip_depth -= 1

    def handle_data(self, data: str) -> None:
        text = " ".join(data.split())
        if self.skip_depth or len(text) < 35:
            return
        self.parts.append(text)


class LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        for name, value in attrs:
            if name == "href" and value:
                self.links.append(html.unescape(value))


def strip_html(value: str) -> str:
    parser = ArticleTextParser()
    parser.feed(value or "")
    return " ".join(parser.parts).strip()


def extract_links(value: str) -> list[str]:
    parser = LinkParser()
    parser.feed(value or "")
    return parser.links


def is_google_news_url(url: str) -> bool:
    host = urllib.parse.urlparse(url).netloc.lower()
    return "news.google." in host or host == "google.com" or host.endswith(".google.com")


def is_useful_article_text(text: str, title: str) -> bool:
    normalized_text = " ".join((text or "").split())
    normalized_title = " ".join((title or "").split())
    if len(normalized_text) < 180:
        return False
    if normalized_text == normalized_title:
        return False
    if normalized_title and normalized_text.startswith(normalized_title) and len(normalized_text) < len(normalized_title) + 80:
        return False
    return True


def article_score(item: NewsItem) -> int:
    score = 0
    if is_useful_article_text(item.article_text, item.title):
        score += min(len(item.article_text), 3000)
    if is_useful_article_text(item.summary, item.title):
        score += min(len(item.summary), 800)
    if item.article_text:
        score += 200
    return score


def select_content_item(items: list[NewsItem], preferred_index: int = 0) -> tuple[NewsItem, int, bool]:
    if not items:
        raise ValueError("선택할 뉴스가 없습니다.")

    if preferred_index > 0:
        index = min(max(preferred_index, 1), len(items))
        return items[index - 1], index, False

    scored_items = [(article_score(item), index, item) for index, item in enumerate(items, start=1)]
    scored_items.sort(key=lambda value: value[0], reverse=True)
    best_score, best_index, best_item = scored_items[0]
    if best_score <= 0:
        return items[0], 1, True
    return best_item, best_index, True


def extract_json_object(value: str) -> dict:
    text = (value or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text, flags=re.IGNORECASE).strip()
        text = re.sub(r"```$", "", text).strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            raise
        return json.loads(match.group(0))


def create_gemini_content(item: NewsItem, article_context: str) -> dict | None:
    api_key = get_secret("GEMINI_API_KEY")
    if not api_key:
        return None

    try:
        from google import genai
    except Exception:
        return None

    model = get_secret("GEMINI_MODEL") or "gemini-3.5-flash"
    prompt = f"""
너는 한국어 뉴스 콘텐츠 에디터다.
아래 기사 정보를 바탕으로 확인된 내용만 사용해 콘텐츠를 작성하라.
본문이 부족해도 콘텐츠 생성을 중단하지 마라.
확인되지 않은 숫자, 발언, 일정, 기업명, 정책명은 지어내지 마라.
대신 제목과 요약에서 확인되는 주제, 일반 배경 설명, 독자가 확인할 체크포인트, 의미 해석을 풍성하게 써라.
정보가 부족하다는 말은 짧게 한 번만 쓰고, 사과하거나 "작성할 수 없다"는 식으로 말하지 마라.

출력은 반드시 JSON 객체 하나만 반환하라.
키는 blog_post, tistory_post, thread_post, slide_script, vrew_script 다섯 개만 사용하라.

조건:
- blog_post: 3000자 이내, 후킹 모드, 블로그 게시용 문체, 제목 포함
- tistory_post: blog_post를 티스토리 블로그용으로 각색. 검색 유입용 제목, 짧은 도입, H2/H3 소제목, 목록, 마무리, 관련 태그 5~8개 포함
- thread_post: 200자 이내, SNS 쓰레드 첫 글로 사용 가능
- slide_script: 유튜브 제작용 슬라이드 6장 구성. 반드시 "## 슬라이드 1."부터 "## 슬라이드 6."까지 위에서 아래로 순서대로 작성하고, 각 장마다 "- 화면 문구:"와 "- 내레이션:"을 포함
- vrew_script: Vrew에 붙여넣기 좋은 장면별 내레이션 대본. 반드시 "## 슬라이드 1."부터 "## 슬라이드 6."까지 위에서 아래로 순서대로 작성

뉴스 제목:
{item.title}

출처:
{item.source}

원문 링크:
{item.link}

수집된 기사 내용:
{article_context}
"""

    try:
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(model=model, contents=prompt)
        data = extract_json_object(response.text)
    except Exception:
        return None

    blog_post = str(data.get("blog_post", "")).strip()[:3000]
    tistory_post = str(data.get("tistory_post", "")).strip()[:3200]
    thread_post = str(data.get("thread_post", "")).strip()[:200]
    slide_script = str(data.get("slide_script", "")).strip()
    vrew_script = str(data.get("vrew_script", "")).strip()

    if not all([blog_post, tistory_post, thread_post, slide_script, vrew_script]):
        return None

    return {
        "blog_post": blog_post,
        "tistory_post": tistory_post,
        "thread_post": thread_post,
        "slide_script": slide_script,
        "vrew_script": vrew_script,
    }


def fetch_article_details(url: str, timeout: int) -> tuple[str, str]:
    if not url:
        return "", ""

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 MorningNewsMailer/2.0",
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.7",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            final_url = response.geturl()
            content_type = response.headers.get("content-type", "")
            if "text/html" not in content_type:
                return "", final_url
            raw_html = response.read(600_000).decode("utf-8", errors="replace")
    except Exception:
        return "", url

    text = extract_article_with_trafilatura(raw_html, final_url) or strip_html(raw_html)
    return text[:2500], final_url


def extract_article_with_trafilatura(raw_html: str, url: str) -> str:
    try:
        import trafilatura
    except Exception:
        return ""

    try:
        extracted = trafilatura.extract(
            raw_html,
            url=url,
            include_comments=False,
            include_tables=False,
            favor_precision=True,
        )
    except Exception:
        return ""

    return " ".join((extracted or "").split())


def fetch_news(query: str, limit: int, timeout: int = 15) -> list[NewsItem]:
    if query:
        encoded_query = urllib.parse.quote(query)
        feed_url = f"https://news.google.com/rss/search?q={encoded_query}&hl=ko&gl=KR&ceid=KR:ko"
    else:
        feed_url = DEFAULT_FEED_URL

    request = urllib.request.Request(feed_url, headers={"User-Agent": "MorningNewsMailer/2.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        root = ET.fromstring(response.read())

    channel = root.find("channel")
    if channel is None:
        return []

    items = []
    for item in channel.findall("item")[:limit]:
        link = item.findtext("link", default="").strip()
        description = item.findtext("description", default="")
        summary = strip_html(description)
        candidate_links = extract_links(description)
        direct_link = next((candidate for candidate in candidate_links if not is_google_news_url(candidate)), "")
        article_text, final_url = fetch_article_details(direct_link or link, timeout)
        if is_google_news_url(final_url):
            final_url = direct_link or link
        items.append(
            NewsItem(
                title=item.findtext("title", default="제목 없음").strip(),
                link=final_url or link,
                source=item.findtext("source", default="Google News").strip(),
                summary=summary,
                article_text=article_text,
            )
        )
    return items


def parse_slide_blocks(slide_script: str) -> list[dict[str, str]]:
    def parse_structured(value: str) -> list[dict[str, str]]:
        text = value.strip()
        if not text or text[0] not in "[{":
            return []
        try:
            parsed = json.loads(text)
        except Exception:
            try:
                parsed = ast.literal_eval(text)
            except Exception:
                return []

        if isinstance(parsed, dict):
            parsed = parsed.get("slides") or parsed.get("slide_script") or parsed.get("items") or []
        if not isinstance(parsed, list):
            return []

        structured: list[dict[str, str]] = []
        for index, item in enumerate(parsed, 1):
            if not isinstance(item, dict):
                continue
            number = item.get("slide_number") or item.get("number") or item.get("slide") or index
            title = item.get("title") or item.get("heading") or ""
            screen = item.get("screen_text") or item.get("screen") or item.get("화면 문구") or item.get("caption") or ""
            narration = item.get("narration") or item.get("voiceover") or item.get("내레이션") or item.get("script") or ""
            structured.append(
                {
                    "title": f"슬라이드 {number}",
                    "screen": str(screen).strip() or str(title).strip(),
                    "narration": str(narration).strip(),
                }
            )
        return structured

    structured = parse_structured(slide_script)
    if structured:
        return structured

    slides: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    mode = ""

    for raw_line in slide_script.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        if line.startswith("##"):
            if current:
                slides.append(current)
            current = {"title": line.lstrip("# ").strip(), "screen": "", "narration": ""}
            mode = ""
            continue

        if current is None:
            continue

        if line.startswith("- 화면 문구:") or line.startswith("화면 문구:"):
            current["screen"] = line.split(":", 1)[1].strip()
            mode = ""
        elif line.startswith("- 내레이션:") or line.startswith("내레이션:"):
            current["narration"] = line.split(":", 1)[1].strip()
            mode = "narration"
        elif line == "화면 문구":
            mode = "screen"
        elif line == "내레이션":
            mode = "narration"
        elif mode == "screen":
            current["screen"] = (current["screen"] + "\n" + line).strip()
        else:
            cleaned = line.lstrip("- ").strip()
            current["narration"] = (current["narration"] + "\n" + cleaned).strip()

    if current:
        slides.append(current)

    return slides


def fallback_slide_blocks(item: NewsItem, article_context: str) -> list[dict[str, str]]:
    return [
        {
            "title": "슬라이드 1. 오프닝",
            "screen": "지금 놓치면 늦습니다",
            "narration": f'오늘 꼭 확인해야 할 뉴스는 "{item.title}"입니다.',
        },
        {
            "title": "슬라이드 2. 무슨 일이 있었나",
            "screen": "핵심 사건 한눈에 보기",
            "narration": f"기사에서 확인한 주요 내용은 다음과 같습니다. {article_context[:180]}",
        },
        {
            "title": "슬라이드 3. 왜 중요한가",
            "screen": "우리에게 미칠 영향",
            "narration": "이 뉴스가 중요한 이유는 관련 시장뿐 아니라 우리의 선택에도 영향을 줄 수 있기 때문입니다.",
        },
        {
            "title": "슬라이드 4. 꼭 볼 세 가지",
            "screen": "배경 · 영향 · 다음 움직임",
            "narration": "변화가 시작된 배경, 실제 영향 범위, 앞으로 나올 후속 움직임을 확인해야 합니다.",
        },
        {
            "title": "슬라이드 5. 대응 방법",
            "screen": "지금 무엇을 준비할까?",
            "narration": "성급한 결론보다 원문과 반대 관점을 함께 확인하고, 내 상황에 맞는 선택지를 준비하세요.",
        },
        {
            "title": "슬라이드 6. 마무리",
            "screen": "다음 변화가 더 중요합니다",
            "narration": "여러분은 이번 뉴스를 어떻게 보셨나요? 의견을 댓글로 남겨 주세요.",
        },
    ]


def ensure_six_slide_blocks(slide_script: str, item: NewsItem, article_context: str) -> list[dict[str, str]]:
    parsed = parse_slide_blocks(slide_script)
    defaults = fallback_slide_blocks(item, article_context)
    blocks: list[dict[str, str]] = []

    for index in range(6):
        source = parsed[index] if index < len(parsed) else {}
        default = defaults[index]
        blocks.append(
            {
                "title": f"슬라이드 {index + 1}",
                "screen": source.get("screen") or default["screen"],
                "narration": source.get("narration") or default["narration"],
            }
        )

    return blocks


def format_slide_script(blocks: list[dict[str, str]], source: str, link: str) -> str:
    sections = ["# 유튜브 슬라이드 대본"]
    for block in blocks:
        sections.append(
            f"""
## {block['title']}
화면 문구
{block['screen']}

내레이션
{block['narration']}
""".strip()
        )
    sections.append(f"출처: {source}\n원문: {link}")
    return "\n\n".join(sections)


def format_vrew_script(blocks: list[dict[str, str]], source: str) -> str:
    sections = ["# Vrew 대본"]
    for block in blocks:
        sections.append(
            f"""
## {block['title']}
화면 문구
{block['screen']}

내레이션
{block['narration']}
""".strip()
        )
    sections.append(f"출처: {source}")
    return "\n\n".join(sections)


def add_pptx_textbox(slide, text: str, left, top, width, height, *, size: int, bold: bool = False, color=None) -> None:
    from pptx.util import Pt

    box = slide.shapes.add_textbox(left, top, width, height)
    frame = box.text_frame
    frame.clear()
    frame.word_wrap = True
    paragraph = frame.paragraphs[0]
    run = paragraph.add_run()
    run.text = text
    run.font.size = Pt(size)
    run.font.bold = bold
    if color is not None:
        run.font.color.rgb = color


def create_basic_pptx(slide_script: str, output_path: Path, title: str) -> str:
    import zipfile

    def esc(value: str) -> str:
        return html.escape(value or "", quote=True)

    def emu(inches: float) -> int:
        return int(inches * 914400)

    def paragraphs(text: str, size: int, color: str = "F8FAFC", bold: bool = False) -> str:
        lines = [line.strip() for line in (text or "").splitlines() if line.strip()] or [""]
        bold_attr = ' b="1"' if bold else ""
        return "".join(
            f'<a:p><a:r><a:rPr lang="ko-KR" sz="{size * 100}"{bold_attr}>'
            f'<a:solidFill><a:srgbClr val="{color}"/></a:solidFill></a:rPr>'
            f"<a:t>{esc(line)}</a:t></a:r></a:p>"
            for line in lines
        )

    def shape(shape_id: int, name: str, text: str, x: float, y: float, w: float, h: float, size: int, *, bold: bool = False, fill: str = "0F172A", color: str = "F8FAFC") -> str:
        return f"""
        <p:sp>
          <p:nvSpPr><p:cNvPr id="{shape_id}" name="{esc(name)}"/><p:cNvSpPr txBox="1"/><p:nvPr/></p:nvSpPr>
          <p:spPr>
            <a:xfrm><a:off x="{emu(x)}" y="{emu(y)}"/><a:ext cx="{emu(w)}" cy="{emu(h)}"/></a:xfrm>
            <a:prstGeom prst="roundRect"><a:avLst/></a:prstGeom>
            <a:solidFill><a:srgbClr val="{fill}"/></a:solidFill>
            <a:ln><a:noFill/></a:ln>
          </p:spPr>
          <p:txBody><a:bodyPr wrap="square" anchor="mid" lIns="152400" rIns="152400" tIns="76200" bIns="76200"/><a:lstStyle/>{paragraphs(text, size, color, bold)}</p:txBody>
        </p:sp>
        """

    def slide_xml(number: int, slide_title: str, screen: str, narration: str) -> str:
        return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
        <p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
          <p:cSld>
            <p:bg><p:bgPr><a:solidFill><a:srgbClr val="050A18"/></a:solidFill><a:effectLst/></p:bgPr></p:bg>
            <p:spTree>
              <p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>
              <p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>
              {shape(2, "Header", slide_title[:60], 0.65, 0.48, 12.0, 0.55, 17, bold=True, fill="111827")}
              {shape(3, "Screen Text", screen[:95], 0.9, 1.45, 11.4, 1.25, 33, bold=True, fill="050A18")}
              {shape(4, "Narration", "내레이션\\n" + narration[:520], 0.9, 3.2, 11.4, 2.55, 18, fill="0F172A")}
              {shape(5, "Footer", f"HYUNTOP NEWS | Slide {number}", 0.7, 6.88, 5.5, 0.25, 9, fill="050A18", color="94A3B8")}
            </p:spTree>
          </p:cSld>
          <p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr>
        </p:sld>"""

    slides_data = parse_slide_blocks(slide_script)
    if not slides_data:
        slides_data = [{"title": "유튜브 슬라이드", "screen": title, "narration": slide_script[:600]}]

    all_slides = slides_data[:6]
    slide_count = len(all_slides)

    content_overrides = "\n".join(
        f'<Override PartName="/ppt/slides/slide{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>'
        for i in range(1, slide_count + 1)
    )
    slide_ids = "\n".join(f'<p:sldId id="{255 + i}" r:id="rId{i}"/>' for i in range(1, slide_count + 1))
    rels = "\n".join(
        f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide{i}.xml"/>'
        for i in range(1, slide_count + 1)
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "[Content_Types].xml",
            f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
            <Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
              <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
              <Default Extension="xml" ContentType="application/xml"/>
              <Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>
              {content_overrides}
            </Types>""",
        )
        archive.writestr(
            "_rels/.rels",
            """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
            <Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
              <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="ppt/presentation.xml"/>
            </Relationships>""",
        )
        archive.writestr(
            "ppt/presentation.xml",
            f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
            <p:presentation xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
              <p:sldIdLst>{slide_ids}</p:sldIdLst>
              <p:sldSz cx="12192000" cy="6858000" type="wide"/>
              <p:notesSz cx="6858000" cy="9144000"/>
            </p:presentation>""",
        )
        archive.writestr(
            "ppt/_rels/presentation.xml.rels",
            f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
            <Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">{rels}</Relationships>""",
        )
        for index, slide_info in enumerate(all_slides, 1):
            archive.writestr(
                f"ppt/slides/slide{index}.xml",
                slide_xml(index, slide_info.get("title", f"슬라이드 {index}"), slide_info.get("screen", ""), slide_info.get("narration", "")),
            )

    return str(output_path)


def create_youtube_pptx(slide_script: str, output_path: Path, title: str) -> str:
    try:
        from pptx import Presentation
        from pptx.dml.color import RGBColor
        from pptx.enum.shapes import MSO_SHAPE
        from pptx.util import Inches, Pt
    except Exception:
        return create_basic_pptx(slide_script, output_path, title)

    slides_data = parse_slide_blocks(slide_script)
    if not slides_data:
        slides_data = [{"title": "유튜브 슬라이드", "screen": title, "narration": slide_script[:600]}]

    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)
    blank_layout = prs.slide_layouts[6]

    bg = RGBColor(5, 10, 24)
    panel = RGBColor(15, 23, 42)
    cyan = RGBColor(34, 211, 238)
    white = RGBColor(248, 250, 252)
    muted = RGBColor(148, 163, 184)

    def set_background(slide) -> None:
        fill = slide.background.fill
        fill.solid()
        fill.fore_color.rgb = bg

    def add_footer(slide, number: int) -> None:
        add_pptx_textbox(slide, f"HYUNTOP NEWS  |  Slide {number}", Inches(0.7), Inches(6.95), Inches(6), Inches(0.25), size=9, color=muted)

    for index, slide_info in enumerate(slides_data[:6], 1):
        slide = prs.slides.add_slide(blank_layout)
        set_background(slide)

        header = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(0.6), Inches(0.5), Inches(12.1), Inches(0.55))
        header.fill.solid()
        header.fill.fore_color.rgb = panel
        header.line.color.rgb = cyan
        header.line.width = Pt(1)
        add_pptx_textbox(slide, slide_info.get("title", f"슬라이드 {index}")[:60], Inches(0.85), Inches(0.61), Inches(10.5), Inches(0.35), size=17, bold=True, color=white)

        screen_text = slide_info.get("screen") or slide_info.get("title", "")
        add_pptx_textbox(slide, screen_text[:95], Inches(0.9), Inches(1.55), Inches(11.4), Inches(1.1), size=33, bold=True, color=white)

        narration_box = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(0.9), Inches(3.25), Inches(11.4), Inches(2.45))
        narration_box.fill.solid()
        narration_box.fill.fore_color.rgb = panel
        narration_box.line.color.rgb = RGBColor(51, 65, 85)
        add_pptx_textbox(slide, "내레이션", Inches(1.2), Inches(3.47), Inches(2), Inches(0.28), size=12, bold=True, color=cyan)
        add_pptx_textbox(slide, slide_info.get("narration", "")[:520], Inches(1.2), Inches(3.9), Inches(10.3), Inches(1.45), size=18, color=white)
        add_footer(slide, index)

    prs.save(output_path)
    return str(output_path)


def create_content_package(item: NewsItem, draft_dir_name: str) -> ContentPackage:
    today = datetime.now().strftime("%Y-%m-%d")
    has_article_text = is_useful_article_text(item.article_text, item.title)
    has_summary = is_useful_article_text(item.summary, item.title)
    article_context = ""
    if has_article_text:
        article_context = item.article_text
    elif has_summary:
        article_context = item.summary
    else:
        article_context = (
            "자동 수집으로는 기사 본문을 충분히 가져오지 못했습니다. "
            "원문 링크를 열어 숫자, 발언, 일정, 배경 정보를 확인한 뒤 보강해야 합니다."
        )

    gemini_content = create_gemini_content(item, article_context)
    if gemini_content:
        blog_post = gemini_content["blog_post"]
        tistory_post = gemini_content["tistory_post"]
        thread_post = gemini_content["thread_post"]
        slide_script = gemini_content["slide_script"]
        vrew_script = gemini_content["vrew_script"]
    else:
        blog_post = f"""# 지금 놓치면 뒤늦게 알게 됩니다: {item.title}

"{item.title}"이라는 소식, 그냥 지나쳐도 될까요?

겉으로는 하나의 뉴스처럼 보이지만, 이 이슈가 앞으로의 흐름을 보여주는 신호일 수 있습니다. 지금 알아야 할 핵심만 빠르게 정리해 보겠습니다.

## 기사에서 확인한 내용

{article_context}

## 왜 지금 주목해야 할까?

사람들이 이 뉴스에 관심을 갖는 이유는 단순히 새로운 사건이기 때문만은 아닙니다. 관련 시장과 우리의 일상에 어떤 변화가 생길지 가늠할 수 있기 때문입니다.

특히 제목에 담긴 핵심 변화가 누구에게 기회가 되고, 누구에게 부담이 될지 살펴봐야 합니다. 원문 기사에 나온 숫자와 일정, 관계자 발언을 함께 확인하면 변화의 크기를 더 정확하게 판단할 수 있습니다.

## 꼭 확인할 세 가지

첫째, 이 변화가 시작된 배경입니다. 갑자기 생긴 일인지, 이전부터 이어진 흐름이 결과로 나타난 것인지 확인해야 합니다.

둘째, 실제 영향 범위입니다. 관련 업계만의 이야기인지, 소비자와 일반 대중의 선택에도 영향을 주는지 살펴볼 필요가 있습니다.

셋째, 다음 움직임입니다. 발표나 사건 자체보다 이후에 나올 정책, 기업 대응, 시장 반응이 더 중요할 수 있습니다.

## 우리가 준비할 것은?

뉴스를 보고 바로 결론을 내리기보다 원문을 확인하고, 반대 관점의 기사도 함께 살펴보는 것이 좋습니다. 변화가 내 일과 생활에 직접 연결된다면 지금부터 선택지를 정리해 두는 것이 유리합니다.

## 결론

이번 뉴스의 핵심은 "{item.title}"입니다. 중요한 것은 소식을 아는 데서 끝내지 않고, 다음 변화가 어디에서 나타날지 한발 먼저 관찰하는 것입니다.

출처: {item.source}
원문: {item.link}
확인일: {today}
"""
        blog_post = blog_post[:3000]

        tistory_post = f"""# {item.title} 핵심 정리: 지금 확인할 포인트

## 이 뉴스가 주목받는 이유

{article_context}

## 핵심 포인트

- 관련 흐름이 왜 지금 나타났는지 확인해야 합니다.
- 실제 영향 범위가 어디까지 이어질지 살펴봐야 합니다.
- 후속 발표, 시장 반응, 관계자 입장을 함께 확인하면 좋습니다.

## 티스토리 독자를 위한 해설

이번 이슈는 단순히 하나의 기사로 소비하기보다, 앞으로 이어질 변화의 방향을 보는 자료로 활용할 수 있습니다. 확인된 정보와 아직 확인되지 않은 정보를 구분해서 읽는 것이 중요합니다.

## 마무리

원문을 함께 확인하면서 숫자, 일정, 발언이 추가로 나오는지 살펴보면 더 깊이 있는 판단이 가능합니다.

### 관련 태그

#뉴스정리 #경제뉴스 #이슈분석 #AI #트렌드 #콘텐츠자동화
"""

        thread_post = (
            f"놓치면 뒤늦게 알게 될 뉴스: {item.title} "
            f"핵심은 {article_context[:70]}... 배경·영향·후속 움직임을 확인하세요. "
            f"원문: {item.link}"
        )[:200]

        slide_script = f"""# 유튜브 슬라이드 대본

## 슬라이드 1. 오프닝
- 화면 문구: 지금 놓치면 늦습니다
- 내레이션: 오늘 꼭 확인해야 할 뉴스는 "{item.title}"입니다.

## 슬라이드 2. 무슨 일이 있었나
- 화면 문구: 핵심 사건 한눈에 보기
- 내레이션: 기사에서 확인한 주요 내용은 다음과 같습니다. {article_context[:180]}

## 슬라이드 3. 왜 중요한가
- 화면 문구: 우리에게 미칠 영향
- 내레이션: 이 뉴스가 중요한 이유는 관련 시장뿐 아니라 우리의 선택에도 영향을 줄 수 있기 때문입니다.

## 슬라이드 4. 꼭 볼 세 가지
- 화면 문구: 배경 · 영향 · 다음 움직임
- 내레이션: 변화가 시작된 배경, 실제 영향 범위, 앞으로 나올 후속 움직임을 확인해야 합니다.

## 슬라이드 5. 대응 방법
- 화면 문구: 지금 무엇을 준비할까?
- 내레이션: 성급한 결론보다 원문과 반대 관점을 함께 확인하고, 내 상황에 맞는 선택지를 준비하세요.

## 슬라이드 6. 마무리
- 화면 문구: 다음 변화가 더 중요합니다
- 내레이션: 여러분은 이번 뉴스를 어떻게 보셨나요? 의견을 댓글로 남겨 주세요.

출처: {item.source}
원문: {item.link}
"""

        vrew_script = f"""[오프닝]
지금 놓치면 뒤늦게 알게 될 수 있습니다.
오늘의 뉴스는, {item.title}입니다.

[장면 1]
먼저 무슨 일이 있었는지 살펴보겠습니다.
기사에서 확인한 주요 내용은 다음과 같습니다.
{article_context[:220]}

[장면 2]
그렇다면 왜 이 뉴스가 중요할까요?
관련 업계만의 이야기가 아니라 우리의 선택과 생활에도 영향을 줄 가능성이 있기 때문입니다.

[장면 3]
꼭 확인할 것은 세 가지입니다.
변화가 시작된 배경.
실제 영향 범위.
그리고 앞으로 나올 후속 움직임입니다.

[장면 4]
뉴스를 보고 바로 결론을 내리기보다 원문과 다른 관점을 함께 확인하세요.
변화가 내 일과 생활에 연결된다면 지금부터 선택지를 준비하는 것이 좋습니다.

[클로징]
사건 자체보다 다음 변화가 더 중요합니다.
여러분의 생각은 어떠신가요?

출처는 {item.source}입니다.
"""

    slide_blocks = ensure_six_slide_blocks(slide_script, item, article_context)
    slide_script = format_slide_script(slide_blocks, item.source, item.link)
    vrew_script = format_vrew_script(slide_blocks, item.source)

    directory_text = ""
    pptx_path = ""
    try:
        draft_dir = BASE_DIR / (draft_dir_name or "blog_drafts")
        draft_dir.mkdir(parents=True, exist_ok=True)
        safe_title = "".join(character for character in item.title if character not in '\\/:*?"<>|')
        safe_title = "-".join(safe_title.split())[:50] or "news-content"
        package_dir = draft_dir / f"{today}-{safe_title}"
        package_dir.mkdir(parents=True, exist_ok=True)
        (package_dir / "01-blog-post.md").write_text(blog_post, encoding="utf-8")
        (package_dir / "02-tistory-post.md").write_text(tistory_post, encoding="utf-8")
        (package_dir / "03-thread-post.txt").write_text(thread_post, encoding="utf-8")
        (package_dir / "04-youtube-slides.md").write_text(slide_script, encoding="utf-8")
        (package_dir / "05-vrew-script.txt").write_text(vrew_script, encoding="utf-8")
        pptx_path = create_youtube_pptx(slide_script, package_dir / "06-youtube-slides.pptx", item.title)
        directory_text = str(package_dir)
    except OSError:
        # Cloud storage may be temporary or read-only; the package still goes into the email.
        pass

    return ContentPackage(
        news_title=item.title,
        blog_post=blog_post,
        tistory_post=tistory_post,
        thread_post=thread_post,
        slide_script=slide_script,
        vrew_script=vrew_script,
        directory=directory_text,
        pptx_path=pptx_path,
    )


def build_email(
    sender: str,
    recipient: str,
    items: list[NewsItem],
    query: str,
    content_package: ContentPackage | None = None,
) -> EmailMessage:
    today = datetime.now().strftime("%Y-%m-%d")
    topic = query or "주요"
    subject = f"[아침 뉴스] {topic} 뉴스 {len(items)}개 - {today}"
    rows = "\n".join(
        f"""
        <li style="margin-bottom:18px">
          <a href="{html.escape(item.link)}" style="font-size:17px;font-weight:700;color:#155eef;text-decoration:none">
            {html.escape(item.title)}
          </a>
          <div style="margin-top:5px;color:#667085;font-size:13px">{html.escape(item.source)}</div>
        </li>
        """
        for item in items
    )
    package_html = ""
    package_plain = ""
    if content_package:
        package_html = f"""
          <hr style="border:none;border-top:1px solid #e5e7eb;margin:28px 0">
          <h2>오늘의 콘텐츠 패키지</h2>
          <p style="color:#667085">선택 뉴스: {html.escape(content_package.news_title)}</p>
          <h3>1. 후킹형 블로그 글</h3>
          <pre style="white-space:pre-wrap;background:#f9fafb;border:1px solid #e5e7eb;padding:16px">{html.escape(content_package.blog_post)}</pre>
          <h3>2. 티스토리용 각색 글</h3>
          <pre style="white-space:pre-wrap;background:#f9fafb;border:1px solid #e5e7eb;padding:16px">{html.escape(content_package.tistory_post)}</pre>
          <h3>3. 쓰레드 글</h3>
          <pre style="white-space:pre-wrap;background:#f9fafb;border:1px solid #e5e7eb;padding:16px">{html.escape(content_package.thread_post)}</pre>
          <h3>4. 유튜브 슬라이드 대본</h3>
          <pre style="white-space:pre-wrap;background:#f9fafb;border:1px solid #e5e7eb;padding:16px">{html.escape(content_package.slide_script)}</pre>
          <h3>5. Vrew 대본</h3>
          <pre style="white-space:pre-wrap;background:#f9fafb;border:1px solid #e5e7eb;padding:16px">{html.escape(content_package.vrew_script)}</pre>
        """
        package_plain = (
            f"\n\n[후킹형 블로그 글]\n{content_package.blog_post}"
            f"\n\n[티스토리용 각색 글]\n{content_package.tistory_post}"
            f"\n\n[쓰레드 글]\n{content_package.thread_post}"
            f"\n\n[유튜브 슬라이드 대본]\n{content_package.slide_script}"
            f"\n\n[Vrew 대본]\n{content_package.vrew_script}"
        )

    body = f"""
    <html lang="ko">
      <body style="background:#f5f7fa;padding:24px;font-family:Arial,'Malgun Gothic',sans-serif">
        <main style="max-width:680px;margin:auto;background:#fff;border:1px solid #e5e7eb;padding:28px">
          <h1 style="margin-top:0">아침 {html.escape(topic)} 뉴스</h1>
          <ol>{rows}</ol>
          {package_html}
        </main>
      </body>
    </html>
    """

    message = EmailMessage()
    message["From"] = sender
    message["To"] = recipient
    message["Subject"] = subject
    message.set_content(
        "\n".join(f"{index}. {item.title}\n{item.link}" for index, item in enumerate(items, 1)) + package_plain
    )
    message.add_alternative(body, subtype="html")
    if content_package and content_package.pptx_path:
        pptx_file = Path(content_package.pptx_path)
        if pptx_file.exists():
            message.add_attachment(
                pptx_file.read_bytes(),
                maintype="application",
                subtype="vnd.openxmlformats-officedocument.presentationml.presentation",
                filename=pptx_file.name,
            )
    return message


def send_email(message: EmailMessage, sender: str, app_password: str) -> None:
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context()) as smtp:
        smtp.login(sender, app_password)
        smtp.send_message(message)


def run_mailer(settings: dict | None = None, dry_run: bool = False) -> dict:
    load_env_file()
    settings = settings or load_settings()
    query = str(settings.get("news_query", os.getenv("NEWS_QUERY", ""))).strip()
    limit = int(settings.get("news_limit", os.getenv("NEWS_LIMIT", "5")))
    timeout = int(settings.get("request_timeout_seconds", os.getenv("REQUEST_TIMEOUT_SECONDS", "15")))
    blog_enabled = bool(settings.get("blog_enabled", False))
    pick_index = int(settings.get("blog_pick_index", 1))
    candidate_limit = int(settings.get("content_candidate_limit", os.getenv("CONTENT_CANDIDATE_LIMIT", "10")))
    fetch_limit = max(limit, candidate_limit) if blog_enabled and pick_index == 0 else limit
    items = fetch_news(query, fetch_limit, timeout)

    if not items:
        return {"ok": True, "sent": 0, "message": "가져온 뉴스가 없습니다."}

    email_items = items[:limit]
    content_package = None
    selected_index = None
    auto_selected = False
    content_message = ""
    if blog_enabled:
        selected_item, selected_index, auto_selected = select_content_item(items, pick_index)
        if auto_selected and article_score(selected_item) <= 0:
            content_message = "본문이 충분한 기사는 없었지만, 제목과 요약을 바탕으로 해설형 콘텐츠를 만들었습니다."
        content_package = create_content_package(
            selected_item,
            str(settings.get("blog_draft_dir", "blog_drafts")),
        )

    if dry_run:
        return {
            "ok": True,
            "sent": 0,
            "items": [item.__dict__ for item in email_items],
            "content_package": content_package.__dict__ if content_package else None,
            "selected_index": selected_index,
            "auto_selected": auto_selected,
            "content_message": content_message,
        }

    sender = get_secret("GMAIL_ADDRESS")
    app_password = get_secret("GMAIL_APP_PASSWORD")
    recipient = str(settings.get("recipient_email") or get_secret("RECIPIENT_EMAIL") or sender).strip()
    if not sender or not app_password or not recipient:
        raise RuntimeError("Gmail 주소, 앱 비밀번호, 받는 이메일 설정이 필요합니다.")

    send_email(build_email(sender, recipient, email_items, query, content_package), sender, app_password)
    return {
        "ok": True,
        "sent": len(email_items),
        "recipient": recipient,
        "content_package": content_package.__dict__ if content_package else None,
        "selected_index": selected_index,
        "auto_selected": auto_selected,
        "content_message": content_message,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    print(json.dumps(run_mailer(dry_run=args.dry_run), ensure_ascii=False))


if __name__ == "__main__":
    main()
