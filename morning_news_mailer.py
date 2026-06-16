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
class ContentPackage:
    news_title: str
    blog_post: str
    thread_post: str
    slide_script: str
    vrew_script: str
    directory: str = ""


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


def create_content_package(item: NewsItem, draft_dir_name: str) -> ContentPackage:
    today = datetime.now().strftime("%Y-%m-%d")
    blog_post = f"""# 지금 놓치면 뒤늦게 알게 됩니다: {item.title}

"{item.title}"이라는 소식, 그냥 지나쳐도 될까요?

겉으로는 하나의 뉴스처럼 보이지만, 이 이슈가 앞으로의 흐름을 보여주는 신호일 수 있습니다. 지금 알아야 할 핵심만 빠르게 정리해 보겠습니다.

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

    thread_post = (
        f"놓치면 뒤늦게 알게 될 뉴스: {item.title} "
        "핵심은 사건 자체보다 다음 변화입니다. 배경·영향 범위·후속 움직임을 확인하세요. "
        f"원문: {item.link}"
    )[:200]

    slide_script = f"""# 유튜브 슬라이드 대본

## 슬라이드 1. 오프닝
- 화면 문구: 지금 놓치면 늦습니다
- 내레이션: 오늘 꼭 확인해야 할 뉴스는 "{item.title}"입니다.

## 슬라이드 2. 무슨 일이 있었나
- 화면 문구: 핵심 사건 한눈에 보기
- 내레이션: 먼저 원문 기사를 기준으로 사건의 배경과 현재 상황을 정리해 보겠습니다.

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
원문 기사를 기준으로 사건의 배경과 현재 상황을 확인해야 합니다.

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

    directory_text = ""
    try:
        draft_dir = BASE_DIR / (draft_dir_name or "blog_drafts")
        draft_dir.mkdir(parents=True, exist_ok=True)
        safe_title = "".join(character for character in item.title if character not in '\\/:*?"<>|')
        safe_title = "-".join(safe_title.split())[:50] or "news-content"
        package_dir = draft_dir / f"{today}-{safe_title}"
        package_dir.mkdir(parents=True, exist_ok=True)
        (package_dir / "01-blog-post.md").write_text(blog_post, encoding="utf-8")
        (package_dir / "02-thread-post.txt").write_text(thread_post, encoding="utf-8")
        (package_dir / "03-youtube-slides.md").write_text(slide_script, encoding="utf-8")
        (package_dir / "04-vrew-script.txt").write_text(vrew_script, encoding="utf-8")
        directory_text = str(package_dir)
    except OSError:
        # Cloud storage may be temporary or read-only; the package still goes into the email.
        pass

    return ContentPackage(
        news_title=item.title,
        blog_post=blog_post,
        thread_post=thread_post,
        slide_script=slide_script,
        vrew_script=vrew_script,
        directory=directory_text,
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
          <h3>2. 쓰레드 글</h3>
          <pre style="white-space:pre-wrap;background:#f9fafb;border:1px solid #e5e7eb;padding:16px">{html.escape(content_package.thread_post)}</pre>
          <h3>3. 유튜브 슬라이드 대본</h3>
          <pre style="white-space:pre-wrap;background:#f9fafb;border:1px solid #e5e7eb;padding:16px">{html.escape(content_package.slide_script)}</pre>
          <h3>4. Vrew 대본</h3>
          <pre style="white-space:pre-wrap;background:#f9fafb;border:1px solid #e5e7eb;padding:16px">{html.escape(content_package.vrew_script)}</pre>
        """
        package_plain = (
            f"\n\n[후킹형 블로그 글]\n{content_package.blog_post}"
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

    content_package = None
    if bool(settings.get("blog_enabled", False)):
        pick_index = int(settings.get("blog_pick_index", 1))
        pick_index = min(max(pick_index, 1), len(items))
        content_package = create_content_package(
            items[pick_index - 1],
            str(settings.get("blog_draft_dir", "blog_drafts")),
        )

    if dry_run:
        return {
            "ok": True,
            "sent": 0,
            "items": [item.__dict__ for item in items],
            "content_package": content_package.__dict__ if content_package else None,
        }

    sender = get_secret("GMAIL_ADDRESS")
    app_password = get_secret("GMAIL_APP_PASSWORD")
    recipient = str(settings.get("recipient_email") or get_secret("RECIPIENT_EMAIL") or sender).strip()
    if not sender or not app_password or not recipient:
        raise RuntimeError("Gmail 주소, 앱 비밀번호, 받는 이메일 설정이 필요합니다.")

    send_email(build_email(sender, recipient, items, query, content_package), sender, app_password)
    return {
        "ok": True,
        "sent": len(items),
        "recipient": recipient,
        "content_package": content_package.__dict__ if content_package else None,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    print(json.dumps(run_mailer(dry_run=args.dry_run), ensure_ascii=False))


if __name__ == "__main__":
    main()
