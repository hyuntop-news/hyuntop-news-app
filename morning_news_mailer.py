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


def create_grounded_article_context(item: NewsItem, article_context: str) -> str:
    api_key = get_secret("GEMINI_API_KEY")
    if not api_key:
        return ""

    try:
        from google import genai
        from google.genai import types
    except Exception:
        return ""

    model = get_secret("GEMINI_GROUNDING_MODEL") or get_secret("GEMINI_MODEL") or "gemini-3.5-flash"
    prompt = f"""
Write in Korean.
Use Google Search to strengthen the news context below.
Do not invent exact numbers, quotes, dates, company names, or policy names unless they are found in sources.
If the original article text is limited, use search results to explain confirmed facts, background, likely impact, and what readers should verify next.
Return only a concise briefing, not JSON.

News title:
{item.title}

Publisher/source:
{item.source}

Original link:
{item.link}

Collected text:
{article_context}

Required structure:
1. Confirmed facts
2. Background
3. Why it matters
4. What to watch next
5. Source notes
"""

    try:
        client = genai.Client(api_key=api_key)
        grounding_tool = types.Tool(google_search=types.GoogleSearch())
        response = client.models.generate_content(
            model=model,
            contents=prompt,
            config=types.GenerateContentConfig(tools=[grounding_tool]),
        )
    except Exception:
        return ""

    text = " ".join((response.text or "").split())
    if len(text) < 180:
        return ""
    return text[:4000]


def create_gemini_content(item: NewsItem, article_context: str, use_grounding: bool = False) -> dict | None:
    api_key = get_secret("GEMINI_API_KEY")
    if not api_key:
        return None

    try:
        from google import genai
        from google.genai import types
    except Exception:
        return None

    model = get_secret("GEMINI_MODEL") or "gemini-2.5-pro"
    prompt = f"""
너는 한국어 블로그 작가다.
뉴스를 '요약 보고서'가 아니라 사람들이 끝까지 읽는 블로그 글로 각색한다.
아래 기사 정보는 글감이다. 기사 내용을 그대로 옮기지 말고, 독자가 흥미를 느끼는 이야기형 블로그 글로 재구성하라.

블로그 글 작성 원칙:
- 첫 제목은 클릭하고 싶게 쓰되 과장 광고처럼 쓰지 마라.
- 첫 문단은 장면, 질문, 반전, 불안, 생활 체감 중 하나로 독자를 붙잡아라.
- "기사에서 확인한 내용", "정보가 부족합니다", "원문 확인 필요", "자동 수집" 같은 표현은 blog_post 본문에 쓰지 마라.
- 본문은 사건 설명보다 '왜 이 일이 중요한지', '독자에게 어떤 의미인지', '앞으로 무엇을 봐야 하는지'를 중심으로 풀어라.
- 확인되지 않은 구체 숫자, 직접 발언, 일정, 기업명, 정책명은 지어내지 마라.
- 일반적인 배경지식, 경제/사회적 맥락, 가능한 시나리오, 독자의 생활과 연결되는 해설은 적극적으로 덧붙여라.
- 문체는 블로그 작가처럼 자연스럽게 써라. 딱딱한 정책 보고서 말투를 피하고, 문단마다 읽는 맛이 있어야 한다.
- 소제목은 감정과 궁금증이 살아 있게 써라. 예: "경제는 숫자보다 먼저 거리에서 멈춘다", "비상사태가 무서운 진짜 이유"
- 마지막은 독자가 생각할 질문이나 관찰 포인트로 마무리하라.

출력은 반드시 JSON 객체 하나만 반환하라.
키는 blog_post, tistory_post, thread_post, slide_script, vrew_script 다섯 개만 사용하라.

조건:
- blog_post: 반드시 3300자 이상 4500자 이내. 블로그 게시용 완성 원고. 제목 포함. 기사 설명 20%, 스토리텔링/해설/배경/전망/독자 관점 80% 비율
- tistory_post: blog_post를 티스토리 블로그용으로 다시 각색. 검색 유입용 제목, 자연스러운 도입, H2/H3 소제목, 목록, 마무리, 관련 태그 5~8개 포함
- thread_post: 280~330자, SNS 첫 글처럼 흥미롭게 작성
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
        if use_grounding:
            grounding_tool = types.Tool(google_search=types.GoogleSearch())
            response = client.models.generate_content(
                model=model,
                contents=(
                    prompt
                    + "\n\nAdditional instruction: Use Google Search grounding to verify and enrich the context before writing. "
                    "Then write it as a polished Korean blog article, not a report or summary. "
                    "Keep the final JSON in Korean."
                ),
                config=types.GenerateContentConfig(
                    tools=[grounding_tool],
                    temperature=0.85,
                    max_output_tokens=8192,
                ),
            )
        else:
            response = client.models.generate_content(
                model=model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.85,
                    max_output_tokens=8192,
                ),
            )
        data = extract_json_object(response.text)
    except Exception:
        return None

    blog_post = str(data.get("blog_post", "")).strip()[:4500]
    tistory_post = str(data.get("tistory_post", "")).strip()[:4500]
    thread_post = ensure_thread_length(str(data.get("thread_post", "")).strip(), item.title, article_context)
    slide_script = str(data.get("slide_script", "")).strip()
    vrew_script = str(data.get("vrew_script", "")).strip()

    if len(blog_post) < 3000:
        return None

    if not all([blog_post, tistory_post, thread_post, slide_script, vrew_script]):
        return None

    return {
        "blog_post": blog_post,
        "tistory_post": tistory_post,
        "thread_post": thread_post,
        "slide_script": slide_script,
        "vrew_script": vrew_script,
    }


def create_derivative_content_from_blog(
    blog_post: str,
    title: str = "",
    source: str = "직접 편집",
    link: str = "",
) -> dict[str, str]:
    title = title.strip() or extract_title_from_text(blog_post, "직접 작성한 블로그 글")
    source = source.strip() or "직접 편집"
    link = link.strip()
    api_key = get_secret("GEMINI_API_KEY")
    data: dict[str, str] | None = None

    if api_key:
        try:
            from google import genai

            model = get_secret("GEMINI_MODEL") or "gemini-2.5-pro"
            prompt = f"""
너는 한국어 블로그/숏폼 콘텐츠 작가다.
아래 블로그 글을 기준 원고로 삼아 티스토리 글, 쓰레드, 유튜브 슬라이드 대본, Vrew 대본을 다시 작성하라.
보고서처럼 요약하지 말고, 독자가 보고 싶어지는 제목과 자연스러운 말투로 각색하라.
블로그 글에 없는 구체적 숫자, 발언, 일정, 기업명은 지어내지 마라.
출력은 JSON 객체 하나만 반환하라.
키는 tistory_post, thread_post, slide_script, vrew_script 네 개만 사용하라.

조건:
- tistory_post: 티스토리 블로그용 각색 글. 검색 유입용 제목, 자연스러운 도입, H2/H3 소제목, 목록, 마무리, 관련 태그 5~8개 포함
- thread_post: 280~330자, SNS 첫 글처럼 궁금증을 만들 것
- slide_script: 유튜브 제작용 슬라이드 6장 구성. 반드시 "## 슬라이드 1."부터 "## 슬라이드 6."까지 위에서 아래로 순서대로 작성하고, 각 장마다 "- 화면 문구:"와 "- 내레이션:" 포함. 화면 문구는 짧고 강하게
- vrew_script: Vrew에 붙여넣기 좋은 자연스러운 말투의 장면별 내레이션 대본. 반드시 "## 슬라이드 1."부터 "## 슬라이드 6."까지 위에서 아래로 순서대로 작성

제목:
{title}

원문 출처:
{source}

원문 링크:
{link}

기준 블로그 글:
{blog_post[:6000]}
"""
            client = genai.Client(api_key=api_key)
            response = client.models.generate_content(model=model, contents=prompt)
            raw_data = extract_json_object(response.text)
            data = {
                "tistory_post": str(raw_data.get("tistory_post", "")).strip()[:4500],
                "thread_post": ensure_thread_length(str(raw_data.get("thread_post", "")).strip(), title, blog_post),
                "slide_script": str(raw_data.get("slide_script", "")).strip(),
                "vrew_script": str(raw_data.get("vrew_script", "")).strip(),
            }
        except Exception:
            data = None

    item = NewsItem(title=title, link=link, source=source, summary=blog_post[:500], article_text=blog_post)
    if not data or not all(data.values()):
        data = fallback_derivative_content_from_blog(blog_post, item)

    slide_blocks = ensure_six_slide_blocks(data["slide_script"], item, blog_post)
    data["slide_script"] = format_slide_script(slide_blocks, source, link)
    data["vrew_script"] = format_vrew_script(slide_blocks, source)
    data["thread_post"] = ensure_thread_length(data["thread_post"], title, blog_post)
    data["tistory_post"] = data["tistory_post"][:4500]
    return data


def ensure_thread_length(thread_post: str, title: str, context: str) -> str:
    thread_post = " ".join((thread_post or "").split())
    if len(thread_post) >= 240:
        return thread_post[:330]

    context_preview = " ".join((context or "").split())[:130]
    expanded = (
        f"{thread_post} "
        f"핵심은 '{title}' 이슈를 단순한 뉴스로 넘기지 않고, 배경과 실제 영향, 다음 움직임까지 함께 보는 것입니다. "
        f"{context_preview} "
        "지금은 결론보다 확인할 포인트를 정리해두는 것이 중요합니다."
    )
    return " ".join(expanded.split())[:330]


def extract_title_from_text(text: str, fallback: str) -> str:
    for line in text.splitlines():
        cleaned = line.strip()
        if cleaned.startswith("#"):
            return cleaned.lstrip("# ").strip()[:90] or fallback
    first_line = next((line.strip() for line in text.splitlines() if line.strip()), "")
    return first_line[:90] or fallback


def fallback_derivative_content_from_blog(blog_post: str, item: NewsItem) -> dict[str, str]:
    title = item.title
    preview = " ".join(blog_post.split())[:900]
    tistory_post = f"""# {title}

## 핵심 요약

{preview}

## 왜 주목해야 할까?

이 글의 핵심은 단순한 뉴스 전달이 아니라, 독자가 지금 확인해야 할 변화의 흐름을 정리하는 데 있습니다. 본문에서 다룬 배경과 영향, 앞으로의 움직임을 나누어 보면 더 명확하게 이해할 수 있습니다.

## 확인할 포인트

- 이 이슈가 시작된 배경
- 관련 시장과 생활에 미칠 영향
- 앞으로 나올 후속 발표나 반응

## 마무리

정확한 판단을 위해서는 원문과 추가 자료를 함께 확인하는 것이 좋습니다. 지금은 변화의 방향을 먼저 이해하고, 다음 움직임을 차분히 지켜볼 시점입니다.

### 관련 태그

#뉴스정리 #이슈분석 #경제뉴스 #콘텐츠제작 #티스토리 #블로그글쓰기
"""
    thread_post = (
        f"{title} 이 이슈는 단순한 뉴스 한 줄보다 앞으로의 흐름을 보여주는 신호에 가깝습니다. "
        "핵심은 배경, 실제 영향, 다음 움직임을 함께 보는 것입니다. 지금 확인할 포인트를 정리해두면 이후 변화에 더 빠르게 대응할 수 있습니다."
    )
    slide_script = f"""# 유튜브 슬라이드 대본
## 슬라이드 1. 오프닝
- 화면 문구: 지금 확인해야 할 이슈
- 내레이션: 오늘은 "{title}" 이슈를 핵심만 빠르게 정리해 보겠습니다.

## 슬라이드 2. 핵심 내용
- 화면 문구: 무엇이 달라졌나
- 내레이션: 블로그 글에서 정리한 핵심은 다음과 같습니다. {preview[:220]}

## 슬라이드 3. 왜 중요한가
- 화면 문구: 중요한 이유
- 내레이션: 이 이슈는 관련 시장과 우리의 선택에 영향을 줄 수 있기 때문에 흐름을 함께 봐야 합니다.

## 슬라이드 4. 꼭 볼 포인트
- 화면 문구: 배경, 영향, 후속 움직임
- 내레이션: 배경이 무엇인지, 실제 영향은 어디까지인지, 다음에 어떤 움직임이 나올지 확인해야 합니다.

## 슬라이드 5. 대응 방법
- 화면 문구: 지금 준비할 것
- 내레이션: 성급한 결론보다 확인된 정보와 반대 관점을 함께 보고 판단하는 것이 좋습니다.

## 슬라이드 6. 마무리
- 화면 문구: 다음 변화가 중요합니다
- 내레이션: 오늘 내용이 도움이 되셨다면 다음 흐름도 함께 확인해 보세요.
"""
    return {
        "tistory_post": tistory_post[:4500],
        "thread_post": ensure_thread_length(thread_post, title, blog_post),
        "slide_script": slide_script,
        "vrew_script": slide_script,
    }


def ensure_blog_min_length(blog_post: str, item: NewsItem, article_context: str, today: str) -> str:
    blog_post = (blog_post or "").strip()
    if len(blog_post) >= 3000:
        return blog_post[:4500]

    context = " ".join((article_context or "").split())
    if not context:
        context = "자동 수집만으로는 기사 본문을 충분히 가져오지 못했습니다. 원문 링크를 열어 발언, 일정, 배경 정보를 확인한 뒤 보강해야 합니다."

    title = item.title.strip() or "오늘의 주요 뉴스"
    source = item.source.strip() or "뉴스"
    link = item.link.strip()

    expansion = f"""

## 왜 이 이슈를 지금 봐야 할까

이 뉴스는 단순히 하루짜리 기사로만 소비하기에는 아쉬운 지점이 있습니다. 제목에 담긴 사건 자체보다 더 중요한 것은 이 사건이 어떤 흐름 위에서 나왔고, 앞으로 어떤 선택과 반응을 불러올 수 있느냐입니다. 특히 경제, 정책, 산업, 시장과 연결된 뉴스라면 발표 직후의 headline보다 그 다음에 이어질 움직임을 읽는 것이 훨씬 중요합니다.

이번 기사에서 확인되는 핵심은 다음과 같습니다. {context[:700]}

다만 자동 수집 단계에서 기사 본문 전체를 충분히 확보하지 못했을 수 있기 때문에, 구체적인 수치와 발언은 원문 확인이 필요합니다. 그래서 이 글에서는 확인된 범위 안에서 해석할 수 있는 의미와 독자가 추가로 살펴봐야 할 포인트를 중심으로 정리하겠습니다. 확인되지 않은 숫자를 억지로 붙이는 것보다, 지금 단계에서 무엇을 봐야 하는지 분명하게 잡아두는 편이 더 안전합니다.

## 첫 번째 포인트: 배경을 봐야 합니다

뉴스를 볼 때 가장 먼저 확인해야 할 것은 왜 지금 이 이야기가 나왔는가입니다. 갑자기 등장한 것처럼 보이는 이슈도 실제로는 이전부터 쌓여 온 흐름의 결과인 경우가 많습니다. 정책 변화, 시장 심리, 기업 실적, 국제 정세, 기술 변화, 소비자 행동 같은 요인이 겹치면서 어느 순간 뉴스로 터져 나오는 식입니다.

따라서 이 기사를 읽을 때도 단순히 제목만 보고 좋다, 나쁘다를 판단하기보다 그 배경을 함께 봐야 합니다. 어떤 이해관계자가 움직였는지, 어떤 제도나 시장 조건이 영향을 줬는지, 이전 기사들과 비교했을 때 달라진 점은 무엇인지 확인해야 합니다. 배경을 보면 기사 하나가 아니라 흐름이 보입니다.

## 두 번째 포인트: 실제 영향 범위를 따져야 합니다

두 번째는 영향 범위입니다. 모든 뉴스가 모든 사람에게 같은 무게로 다가오지는 않습니다. 어떤 뉴스는 특정 업계에만 영향을 주고, 어떤 뉴스는 소비자 물가나 투자 심리처럼 우리 일상과 직접 연결됩니다. 또 어떤 뉴스는 당장 큰 변화가 없어 보여도 몇 달 뒤 정책이나 시장 가격에 반영되기도 합니다.

이 이슈도 마찬가지입니다. 관련 업계, 투자자, 소비자, 정책 담당자에게 각각 어떤 의미가 있는지 나눠서 봐야 합니다. 특히 돈의 흐름, 비용 구조, 수요 변화, 규제 가능성, 경쟁 구도와 연결되는 부분이 있다면 그 영향은 생각보다 오래갈 수 있습니다.

## 세 번째 포인트: 숫자보다 방향을 먼저 봐야 합니다

뉴스에서 숫자는 중요하지만, 숫자 하나만으로 전체를 판단하면 위험합니다. 중요한 것은 방향입니다. 좋아지고 있는지, 나빠지고 있는지, 속도가 빨라지는지, 시장의 반응이 일시적인지 지속적인지 봐야 합니다. 구체적인 수치가 부족한 기사일수록 더더욱 방향성을 먼저 확인해야 합니다.

원문 기사에서 추가로 확인하면 좋은 것은 세 가지입니다. 첫째, 실제 수치나 일정입니다. 둘째, 관계자 발언입니다. 셋째, 이후 후속 조치입니다. 이 세 가지가 확인되면 뉴스의 무게가 훨씬 선명해집니다. 반대로 이 세 가지가 비어 있다면 성급한 판단을 피하는 것이 좋습니다.

## 독자가 바로 확인하면 좋은 질문

이 뉴스를 읽고 나서 저에게 남는 질문은 명확합니다. 이 변화가 단기적인 움직임인지, 아니면 구조적인 변화의 시작인지 확인해야 합니다. 또 이 이슈가 특정 기업이나 업계에만 영향을 주는지, 아니면 더 넓은 시장과 생활비, 투자 판단, 정책 방향까지 이어질 수 있는지도 봐야 합니다.

독자는 다음 질문을 기준으로 원문을 다시 확인해보면 좋습니다.

- 이 뉴스의 직접적인 당사자는 누구인가?
- 실제 수치나 일정이 기사 안에 명확히 제시되어 있는가?
- 관계자 발언은 단순한 전망인가, 실행 계획인가?
- 시장이나 소비자에게 영향을 주는 경로는 무엇인가?
- 앞으로 추가 발표나 후속 기사가 나올 가능성이 있는가?

이 질문에 답이 쌓이면 단순한 뉴스 소비가 아니라 판단 가능한 정보로 바뀝니다.

## 블로그 관점에서의 해석

블로그 글에서는 뉴스를 그대로 옮기는 것보다 독자가 이해하기 쉽게 해석하는 것이 중요합니다. 제목은 관심을 끌 수 있어야 하지만, 본문은 과장보다 정리가 우선입니다. 지금처럼 기사 본문이 충분히 수집되지 않은 경우에는 확인된 내용과 추정 가능한 해석을 분리해서 쓰는 것이 좋습니다.

확인된 내용은 짧고 분명하게 정리하고, 부족한 부분은 원문 확인 필요라고 표시하는 방식이 안전합니다. 그래야 독자가 글을 읽으면서도 어디까지가 사실이고 어디부터가 해석인지 구분할 수 있습니다. 특히 경제나 정책 뉴스는 숫자 하나가 의미를 크게 바꿀 수 있기 때문에 이런 구분이 더 중요합니다.

## 마무리

이번 뉴스의 핵심은 {title}입니다. 아직 기사 본문 전체가 충분히 확보되지 않았을 수 있기 때문에 단정적인 결론을 내리기는 어렵습니다. 하지만 이 이슈가 던지는 신호는 가볍지 않습니다. 배경, 영향 범위, 후속 움직임을 차례대로 확인하면 앞으로의 흐름을 더 정확하게 읽을 수 있습니다.

지금 단계에서 가장 좋은 태도는 빠른 결론보다 차분한 확인입니다. 원문을 열어 수치와 발언을 점검하고, 이어지는 후속 기사까지 살펴본다면 이 뉴스가 단순한 하루짜리 소식인지, 아니면 더 큰 변화의 시작인지 판단하는 데 도움이 될 것입니다.

출처: {source}
원문: {link}
확인일: {today}
"""

    return f"{blog_post}\n{expansion}".strip()[:4500]


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


def create_content_package(item: NewsItem, draft_dir_name: str) -> ContentPackage | None:
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

    gemini_content = create_gemini_content(item, article_context, use_grounding=not has_article_text)
    if not gemini_content:
        return None

    blog_post = gemini_content["blog_post"]
    tistory_post = gemini_content["tistory_post"]
    thread_post = gemini_content["thread_post"]
    slide_script = gemini_content["slide_script"]
    vrew_script = gemini_content["vrew_script"]

    if len(blog_post) < 3000:
        return None

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
        if content_package is None:
            content_message = (
                "Gemini Pro가 3000자 이상 콘텐츠를 제대로 만들지 못해 콘텐츠 패키지를 저장하지 않았습니다. "
                "API 한도, 모델명, Google Search grounding 사용 가능 여부를 확인해 주세요."
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
