import argparse
import html
import json
import os
import smtplib
import ssl
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
SETTINGS_PATH = BASE_DIR / "settings.json"
DEFAULT_FEED_URL = "https://news.google.com/rss?hl=ko&gl=KR&ceid=KR:ko"


@dataclass
class NewsItem:
    title: str
    link: str
    source: str


@dataclass
class BlogDraft:
    news_title: str
    content: str
    path: str = ""


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
    return json.loads(path.read_text(encoding="utf-8"))


def get_secret(name: str) -> str:
    value = os.getenv(name, "").strip()
    if value:
        return value

    try:
        import streamlit as st

        return str(st.secrets.get(name, "")).strip()
    except Exception:
        return ""


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
        items.append(
            NewsItem(
                title=item.findtext("title", default="제목 없음").strip(),
                link=item.findtext("link", default="").strip(),
                source=item.findtext("source", default="Google News").strip(),
            )
        )
    return items


def create_blog_draft(item: NewsItem, draft_dir_name: str) -> BlogDraft:
    today = datetime.now().strftime("%Y-%m-%d")
    content = f"""# {item.title}

## 한 줄 요약
{item.title}

## 도입
오늘 살펴볼 뉴스는 "{item.title}"입니다. 이 소식이 주목받는 이유와 앞으로 확인할 부분을 정리해 보겠습니다.

## 핵심 내용
- 출처: {item.source}
- 원문 링크: {item.link}
- 확인 날짜: {today}

## 블로그 본문 초안
이번 뉴스에서 가장 먼저 살펴볼 점은 이 이슈가 지금 주목받는 배경입니다. 제목만 전달하기보다 관련된 흐름과 독자에게 미칠 수 있는 영향을 함께 설명하면 이해하기 쉬운 글이 됩니다.

두 번째로 확인할 부분은 구체적인 사실입니다. 원문 기사에서 숫자, 일정, 관계자 발언을 확인해 본문에 보강하면 글의 신뢰도가 높아집니다.

마지막으로 앞으로 지켜볼 변화도 중요합니다. 이번 소식이 시장이나 일상에 어떤 영향을 줄지 생각해 보고, 독자가 확인하면 좋을 내용을 제안할 수 있습니다.

## 마무리
이 뉴스는 현재의 흐름을 이해할 수 있는 좋은 출발점입니다. 원문을 확인해 구체적인 사실과 개인적인 해석을 보강하면 게시 가능한 블로그 글로 완성할 수 있습니다.
"""

    path_text = ""
    try:
        draft_dir = BASE_DIR / (draft_dir_name or "blog_drafts")
        draft_dir.mkdir(parents=True, exist_ok=True)
        safe_title = "".join(character for character in item.title if character not in '\\/:*?"<>|')
        safe_title = "-".join(safe_title.split())[:60] or "news-blog-draft"
        draft_path = draft_dir / f"{today}-{safe_title}.md"
        draft_path.write_text(content, encoding="utf-8")
        path_text = str(draft_path)
    except OSError:
        # Cloud storage may be temporary or read-only; the draft still goes into the email.
        pass

    return BlogDraft(news_title=item.title, content=content, path=path_text)


def build_email(
    sender: str,
    recipient: str,
    items: list[NewsItem],
    query: str,
    blog_draft: BlogDraft | None = None,
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
    draft_html = ""
    draft_plain = ""
    if blog_draft:
        draft_html = f"""
          <hr style="border:none;border-top:1px solid #e5e7eb;margin:28px 0">
          <h2>오늘의 블로그 초안</h2>
          <p style="color:#667085">선택 뉴스: {html.escape(blog_draft.news_title)}</p>
          <pre style="white-space:pre-wrap;background:#f9fafb;border:1px solid #e5e7eb;padding:16px;font-family:Arial,'Malgun Gothic',sans-serif;line-height:1.6">{html.escape(blog_draft.content)}</pre>
        """
        draft_plain = f"\n\n오늘의 블로그 초안\n\n{blog_draft.content}"

    body = f"""
    <html lang="ko">
      <body style="background:#f5f7fa;padding:24px;font-family:Arial,'Malgun Gothic',sans-serif">
        <main style="max-width:680px;margin:auto;background:#fff;border:1px solid #e5e7eb;padding:28px">
          <h1 style="margin-top:0">아침 {html.escape(topic)} 뉴스</h1>
          <ol>{rows}</ol>
          {draft_html}
        </main>
      </body>
    </html>
    """

    message = EmailMessage()
    message["From"] = sender
    message["To"] = recipient
    message["Subject"] = subject
    message.set_content(
        "\n".join(f"{index}. {item.title}\n{item.link}" for index, item in enumerate(items, 1)) + draft_plain
    )
    message.add_alternative(body, subtype="html")
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
    items = fetch_news(query, limit, timeout)

    if not items:
        return {"ok": True, "sent": 0, "message": "가져온 뉴스가 없습니다."}

    blog_draft = None
    if bool(settings.get("blog_enabled", False)):
        pick_index = int(settings.get("blog_pick_index", 1))
        pick_index = min(max(pick_index, 1), len(items))
        blog_draft = create_blog_draft(
            items[pick_index - 1],
            str(settings.get("blog_draft_dir", "blog_drafts")),
        )

    if dry_run:
        return {
            "ok": True,
            "sent": 0,
            "items": [item.__dict__ for item in items],
            "blog_draft": blog_draft.__dict__ if blog_draft else None,
        }

    sender = get_secret("GMAIL_ADDRESS")
    app_password = get_secret("GMAIL_APP_PASSWORD")
    recipient = str(settings.get("recipient_email") or get_secret("RECIPIENT_EMAIL") or sender).strip()
    if not sender or not app_password or not recipient:
        raise RuntimeError("Gmail 주소, 앱 비밀번호, 받는 이메일 설정이 필요합니다.")

    send_email(build_email(sender, recipient, items, query, blog_draft), sender, app_password)
    return {
        "ok": True,
        "sent": len(items),
        "recipient": recipient,
        "blog_draft": blog_draft.__dict__ if blog_draft else None,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    print(json.dumps(run_mailer(dry_run=args.dry_run), ensure_ascii=False))


if __name__ == "__main__":
    main()
