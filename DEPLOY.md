# GitHub + Streamlit Cloud 배포

## 절대 GitHub에 올리지 않는 파일

- `.env`
- `.streamlit/secrets.toml`
- `settings.json`
- `logs/`
- `blog_drafts/`

위 파일들은 `.gitignore`에 등록되어 있습니다.

## 1. GitHub 저장소에 올리기

GitHub에서 새 저장소를 만든 뒤 이 폴더의 파일들을 업로드합니다.

필수 파일:

- `ui.py`
- `morning_news_mailer.py`
- `requirements.txt`
- `.streamlit/config.toml`
- `.github/workflows/morning-news.yml`
- `settings.example.json`

## 2. Streamlit 화면 배포

1. `https://share.streamlit.io` 접속
2. `Create app` 클릭
3. GitHub 저장소와 브랜치 선택
4. Main file path에 `ui.py` 입력
5. Advanced settings의 Secrets에 아래 형식으로 입력

```toml
GMAIL_ADDRESS = "보내는 Gmail 주소"
GMAIL_APP_PASSWORD = "Gmail 앱 비밀번호"
RECIPIENT_EMAIL = "받는 이메일 주소"
```

6. Deploy 클릭

## 3. 매일 오전 7시 자동 발송

GitHub 저장소의 Settings > Secrets and variables > Actions에서 다음 Repository secrets를 만듭니다.

- `GMAIL_ADDRESS`
- `GMAIL_APP_PASSWORD`
- `RECIPIENT_EMAIL`

이후 Actions 탭에서 `Send morning news` 워크플로를 수동 실행해 테스트합니다.

예약 시간이나 뉴스 키워드는 `.github/workflows/morning-news.yml`에서 변경합니다.
