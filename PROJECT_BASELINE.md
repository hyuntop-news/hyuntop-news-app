# HYUNTOP NEWS 작업 기준표

이 문서는 앱 작업 기준을 고정하기 위한 약속입니다.
앞으로 코드 수정, 로컬 테스트, 배포 확인은 이 기준을 먼저 확인한 뒤 진행합니다.

## 1. 기준 작업 폴더

현재 기준 작업 폴더는 아래입니다.

```text
C:\Users\user\Documents\Codex\2026-05-23\5
```

이 폴더를 기준본으로 봅니다.

## 2. 기준 GitHub 저장소

GitHub Desktop에서 선택해야 하는 저장소:

```text
hyuntop-news-app
```

브랜치:

```text
main
```

## 3. 기준 배포 앱

Streamlit Cloud 배포 앱은 GitHub의 `main` 브랜치와 `ui.py`를 기준으로 실행합니다.

배포 주소 형식:

```text
https://...streamlit.app/
```

주의:

- `http://localhost:8501/`은 로컬 전용 주소입니다.
- 카페, 블로그, 외부 공유에는 `streamlit.app` 주소만 사용합니다.

## 4. 로컬과 배포의 차이

로컬 화면:

- 이 컴퓨터에서 PowerShell 또는 VS Code 터미널로 Streamlit을 켠 화면입니다.
- 주소는 `http://localhost:8501/`입니다.
- 이 컴퓨터에서만 열립니다.

배포 화면:

- Streamlit Cloud에서 실행되는 화면입니다.
- GitHub에 Push된 코드와 Streamlit Secrets를 사용합니다.
- 다른 기기와 외부 링크에서 열 수 있습니다.

## 5. 설정값 기준

로컬 설정:

- `.env`
- `settings.json`

배포 설정:

- Streamlit Secrets
- 배포 서버의 저장 상태

주의:

- 로컬 `.env`를 고쳐도 배포 Secrets는 자동으로 바뀌지 않습니다.
- 로컬 `settings.json`을 고쳐도 배포 화면 설정은 자동으로 같아지지 않을 수 있습니다.

## 6. 콘텐츠 저장 기준

생성된 글과 영상 자료는 주로 아래 폴더에 저장됩니다.

```text
blog_drafts
```

주의:

- 코드를 고쳐도 이미 저장된 글이 자동으로 새 스타일로 바뀌지는 않습니다.
- 블로그 글을 수정한 뒤 티스토리, 쓰레드, 유튜브 대본을 다시 만들려면 앱 안의 재생성 기능을 사용합니다.

## 7. 앞으로 수정 절차

앞으로 앱 기능을 수정할 때는 아래 순서로 진행합니다.

1. 기준 폴더가 맞는지 확인
2. 코드 수정
3. 문법 검사
4. 로컬 화면에서 기능 확인
5. GitHub Desktop에서 Summary 입력
6. `Commit to main`
7. `Push origin`
8. Streamlit Cloud 배포 화면 확인
9. 필요하면 Streamlit Cloud에서 Reboot

## 8. 완료라고 말하기 전 확인 기준

앞으로 "완료"라고 말하기 전 최소 확인 기준:

1. 코드 문법 오류가 없는지 확인
2. 수정한 파일이 기준 폴더 안에 있는지 확인
3. 로컬과 배포 중 어디까지 반영됐는지 구분해서 안내
4. GitHub Desktop에 넣을 Summary 문구 안내

## 9. 기능별 핵심 파일

대시보드 화면:

```text
ui.py
```

뉴스 수집, 메일 발송, 콘텐츠 생성:

```text
morning_news_mailer.py
```

배포 의존성:

```text
requirements.txt
packages.txt
```

GitHub Actions 자동 실행:

```text
.github\workflows\morning-news.yml
```

## 10. 헷갈릴 때 기준 질문

문제가 생기면 먼저 아래를 확인합니다.

1. 지금 보고 있는 화면이 `localhost`인가, `streamlit.app`인가?
2. 지금 수정한 폴더가 기준 폴더인가?
3. GitHub Desktop에서 Push까지 했는가?
4. 배포 Secrets와 로컬 `.env`가 서로 같은가?
5. 이미 저장된 예전 콘텐츠를 보고 있는 것은 아닌가?
