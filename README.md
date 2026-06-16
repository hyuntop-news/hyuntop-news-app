# 아침 주요뉴스 Gmail 발송 앱

매일 아침 한국어 Google 뉴스 주요뉴스 5개를 Gmail로 보내는 작은 앱입니다. Windows 기본 PowerShell만으로 실행되며, Python 버전도 함께 들어 있습니다.

## 1. 설정

`.env.example` 파일을 복사해서 `.env` 파일을 만들고 값을 채워 주세요.

```powershell
Copy-Item .env.example .env
```

필수 값:

- `GMAIL_ADDRESS`: 보내는 Gmail 주소
- `GMAIL_APP_PASSWORD`: Gmail 앱 비밀번호 16자리
- `RECIPIENT_EMAIL`: 받을 이메일 주소

Gmail은 일반 계정 비밀번호로 SMTP 로그인을 허용하지 않습니다. Google 계정에서 2단계 인증을 켠 뒤 앱 비밀번호를 만들어 넣어 주세요.

## 2. 바로 테스트

```powershell
.\Send-MorningNews.ps1 -DryRun
```

메일 발송까지 테스트하려면:

```powershell
.\Send-MorningNews.ps1
```

Python이 설치되어 있다면 아래 파일로도 실행할 수 있습니다.

```powershell
py morning_news_mailer.py
```

성공하면 `RECIPIENT_EMAIL` 주소로 뉴스 메일이 도착합니다.

## 3. 매일 아침 자동 실행

아침 7시에 보내려면 PowerShell에서 실행하세요.

```powershell
.\schedule_daily.ps1 -Time "07:00"
```

다른 시간으로 바꾸려면 예를 들어:

```powershell
.\schedule_daily.ps1 -Time "08:30"
```

## 설정 화면

Streamlit 화면으로 키워드, 실행 시간, 수신 이메일, 콘텐츠 제작 설정을 바꿀 수 있습니다.

```powershell
streamlit run ui.py
```

설정은 `settings.json`에 저장됩니다. Gmail 주소와 앱 비밀번호는 보안상 기존 `.env` 파일에 계속 둡니다.

## 옵션

특정 주제만 받고 싶으면 `.env`에 `NEWS_QUERY`를 추가하세요.

```env
NEWS_QUERY=경제
```

뉴스 중 하나를 골라 콘텐츠 패키지까지 만들고 싶으면 `.env`에 아래 값을 추가하세요.

```env
BLOG_ENABLED=true
BLOG_PICK_INDEX=1
BLOG_DRAFT_DIR=blog_drafts
```

`BLOG_PICK_INDEX=1`은 뉴스 목록의 첫 번째 뉴스를 고른다는 뜻입니다. `2`로 바꾸면 두 번째 뉴스를 고릅니다.

선택한 뉴스로 아래 결과물을 만듭니다.

- 3,000자 이내 후킹형 블로그 글
- 200자 이내 쓰레드 글
- 유튜브 제작용 슬라이드 대본
- Vrew 영상 제작용 대본

결과물은 `blog_drafts` 아래의 날짜별 콘텐츠 폴더에 각각 저장되고, 메일에도 같이 들어갑니다.

안정장치를 조정하려면 `.env`에 아래 값을 넣을 수 있습니다.

```env
RETRY_COUNT=3
RETRY_DELAY_SECONDS=3
REQUEST_TIMEOUT_SECONDS=10
ERROR_EMAIL_ENABLED=true
```

실행 기록은 `logs/morning-news.log` 파일에 저장됩니다. 뉴스가 0개면 오류로 멈추지 않고 조용히 건너뜁니다. 오류 메일 알림을 켜면 실패 내용이 받는 메일 주소로 전송됩니다.

직접 RSS 주소를 쓰고 싶으면 `NEWS_FEED_URL`을 넣으면 됩니다.
