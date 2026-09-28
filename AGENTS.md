# brain_fast 공통 하네스

이 파일이 이 저장소의 유일한 작업 기준이다. Cursor, Codex, Copilot, Claude, Gemini, Windsurf 등 어떤 도구로 작업하든 이 내용을 따른다. 다른 지침 파일과 어긋나면 이 파일을 따른다.

개인 PC에서만 쓸 예외는 `AGENTS.override.md`에 적는다. 그 파일은 커밋하지 않는다.

## 이 프로젝트가 하는 일

브라우저 요청을 FastAPI가 받아 PostgreSQL에 저장하거나 HTML 화면을 돌려준다. 영상 분석은 부위마다 `class/<패키지>/`에 둔다. 지금은 `class/brain/`(뇌 CT)만 있다.

## 폴더 구조

```
brain_fast/
  main.py            서버 시작. 라우터를 여기에 연결한다
  config.py          DB, 세션, NAS 설정 (.env)
  db.py              DB 연결
  routers/           주소. 관리자 / 학습자 API / 뇌 분석
  schemas/           요청·응답 형식
  services/          업무 규칙
  repositories/      DB 조회·저장
  models/            테이블
  class/<패키지>/    부위·영상별 분석. 아래 class/ 표를 따른다
  templates/         HTML. 관리자는 admin/
  assets/            CSS, JS, 이미지
  utils/             로그인, 비밀번호, SSE 같은 공통 도우미
  docker/            컨테이너, DB 초기 SQL
  tests/             pytest
  docs/              학습용 HTML. 동작 코드를 두지 않는다
```

화면 요청은 `routers` → `schemas` → `services` → `repositories` → `models` 순서로만 흐른다. 분석 요청은 그 부위의 라우터가 `class/<패키지>/`를 호출한다. 뇌 CT는 `routers/brain.py` → `class/brain/`.

## 코드를 넣는 곳

요청은 아래 순서로만 흐른다.

1. `routers/` — URL. 요청을 받아 service로 넘긴다.
2. `schemas/` — 요청·응답 형식. 검증에 실패하면 422.
3. `services/` — 업무 규칙.
4. `repositories/` — DB 조회·저장.
5. `models/` — 테이블 설계. 긴 업무 로직을 넣지 않는다.

화면은 `templates/`(HTML)와 `assets/`(CSS, JS, 이미지)다. 관리자 화면은 `templates/admin/`과 `assets/js/admin/`. 공통 레이아웃은 `templates/admin/partials/layout_adm.html`.

| 하려는 일 | 위치 |
|---|---|
| 새 URL/API | `routers/` |
| 업무 규칙 | `services/` |
| DB 조회·저장 | `repositories/` |
| 테이블·컬럼 | `models/` |
| 입출력 필드 | `schemas/` |
| 관리자 HTML | `templates/admin/` |
| 버튼·스타일 | `assets/js/`, `assets/css/` |
| 로그인·비밀번호·SSE처럼 여러 기능이 공유하는 도우미 | `utils/` |
| 영상 분석 | `class/<패키지>/` (부위마다 하나) |
| Docker·DB 초기화 | `docker/` |
| 테스트 | `tests/` |

새 최상위 폴더를 만들지 않는다. `.venv/`, `.git/`, `__pycache__/`, `logs/`는 수정하지 않는다.

## class/

`class/`는 영상 분석 전용이다. 로그인, Q&A, 스터디 같은 CRUD는 넣지 않는다. 부위·영상마다 패키지를 하나 만들고, 다른 부위의 코드를 그 안에 넣지 않는다.

| 패키지 | 내용 | 상태 |
|---|---|---|
| `class/brain/` | 뇌 CT (출혈 + germinoma) | 있음 |
| `class/thorax/` | 흉부 | 팀원이 추가 |
| `class/abdomen/` | 복부 | 팀원이 추가 |
| `class/knee/` | 무릎 | 팀원이 추가 |
| `class/brain_mri/` | 뇌 MRI | 팀원이 추가. `class/brain/`과 섞지 않는다 |

패키지 이름은 소문자 영문이다. 학습 부위 코드는 `brain` / `thorax`(별칭 chest) / `abdomen` / `knee` 이고, 영상 종류는 `xray` / `ct` / `mri` 다. NAS 폴더의 `Abdomen`, `Knee` 대문자를 패키지 이름에 쓰지 않는다.

새 패키지는 `class/brain/`의 파일 역할을 따른다. 필요한 파일만 둔다.

| 파일 | 하는 일 |
|---|---|
| `preprocess.py` | 업로드 이미지를 그 부위의 모델 입력으로 변환 |
| `ensemble.py` | 그 부위의 분류 모델 |
| `localization.py` | Grad-CAM과 overlay |
| `medgemma.py` | 한국어 판독문. 백엔드 `api` / `template` / `local` |
| `report_format.py` | 판독 프롬프트와 후처리 |
| `constants.py` | 그 부위의 클래스 순서, 한국어 이름, overlay 색 |
| `config.py` | 그 부위의 환경 변수. 루트 `config.py`의 DB·NAS 설정과 섞지 않는다 |

라우터는 호출만 한다. 뇌 CT의 입구는 `routers/brain.py`다. 다른 부위도 같은 방식으로 라우터를 두고 `main.py`에 등록한다.

폴더 이름 `class`는 파이썬 예약어다. `from class.brain import ...`는 쓸 수 없다.

```python
from importlib import import_module

brain = import_module("class.brain")
brain.ensemble_service.predict_all(...)
# 흉부면 import_module("class.thorax")
```

가중치는 Hugging Face 저장소에서 받는다. 체크포인트 파일을 저장소에 넣지 않는다. `HF_TOKEN` 값은 코드와 응답에 적지 않는다. 클래스 순서는 그 패키지의 `constants.py`에만 두고, 학습 때 순서를 유지한다.

## 이름

같은 기능은 접미사를 맞춘다.

- `userServices.py`, `userRepositories.py`, `userModel.py`, `userSchemas.py`
- 관리자 JS는 화면과 짝을 맞춘다. 예: `templates/admin/study.html` ↔ `assets/js/admin/study-manager.js`

router 함수는 얇게 유지한다. DB 세션과 스키마 객체를 service에 넘기고, SQL과 업무 판단은 service/repository에 둔다.

## 기능 하나를 넣을 때

1. `models/`에 컬럼·테이블
2. `schemas/`에 입출력
3. `repositories/`에 저장·조회
4. `services/`에 규칙
5. `routers/`에서 연결 (`main.py`에 라우터 등록이 필요하면 그때만)
6. `templates/`와 `assets/`에 화면
7. `docs/<이름>.html`에 학습 설명. `docs/index.html`에 링크
8. `tests/`에 검증

## 학습용 HTML

기능을 새로 만들거나 `class/<패키지>/`를 추가·고칠 때마다 `docs/<이름>.html`을 만들거나 고친다. 목록은 `docs/index.html`에 링크한다. 브라우저에서 그 파일을 열면 된다. 서버 주소에 붙이지 않는다.

“AI를 만들었다”만 쓰지 않는다. 학습자가 각 부분이 왜 있는지 알 수 있게, 파일·단계마다 아래를 한국어로 적는다.

- 한 줄로 하는 일
- 무엇을 받는지
- 무엇을 하는지
- 무엇을 돌려주는지
- 하지 않는 일
- 헷갈리기 쉬운 점

비밀값, 토큰, 체크포인트 파일은 문서에 적지 않는다. 분석 결과는 학습용 보조이고 진단이 아니라고 적는다.

## 기능 명세서

아래는 이미 있는 기능이다. 새 최상위 기능을 스스로 만들지 않는다. 요청받은 항목만 고치고, 고칠 때는 그 기능의 화면·API·저장이 한 세트로 맞는지 확인한다.

**화면 공유 분석** (`/`, `/api/v1`)

- 업로드 이미지와 ROI를 받아 출혈 6클래스와 germinoma를 추론한다.
- overlay와 한국어 판독문을 돌려준다.
- `NAS_UPLOAD_ON_ANALYZE`가 켜져 있으면 원본, overlay, `result.json`을 NAS에 올린다.
- 흉부·복부·무릎·뇌 MRI는 각각 `class/thorax/`, `class/abdomen/`, `class/knee/`, `class/brain_mri/`에 둔다. 요청받은 부위만 추가한다.

**학습자 API** (prefix 없음)

- 로그인, 노출 중 팝업, Q&A 작성·조회·메시지.
- 의학 용어 사전.
- 학습 문제 1건, 병명 제출과 다음 문제.
- ROI 채점과 리뷰 저장.

**관리자** (`/admin`, 세션 로그인)

- 대시보드, 회원, 리뷰, 스터디, 팝업, Q&A.
- 스터디·팝업 이미지는 NAS에 두고 DB에는 공개 경로만 저장한다.
- 회원·리뷰·스터디·Q&A 목록은 SSE로 갱신할 수 있다.

## 구현 체크리스트

기능을 만들거나 고친 뒤 해당하는 항목을 확인한다. 빈 칸은 남은 할 일이 아니다.

- 어느 기능(화면 공유 분석 / 학습자 API / 관리자)인지 정했다.
- URL은 `routers/`, 입출력은 `schemas/`, 규칙은 `services/`, DB는 `repositories/`, 테이블은 `models/`에 두었다.
- 분석 로직은 그 부위의 `class/<패키지>/`에 두었고, `importlib.import_module("class.<패키지>")`로 호출했다.
- 관리자 화면이면 `templates/admin/`과 `assets/js/admin/`을 같이 고쳤다.
- 이미지가 있으면 NAS 경로와 DB에 저장하는 공개 경로를 둘 다 맞췄다.
- 정답·비밀번호·토큰을 학습자 응답이나 로그에 넣지 않았다.
- 입력 오류는 422, 권한 없음은 관리자 세션 기준, 그 외 서버 오류는 기존 예외 처리로 남긴다.
- `pytest`와, 화면을 바꿨으면 그 화면의 조회·저장을 확인했다.
- `docs/<이름>.html`에 각 부분 설명을 넣었고, `docs/index.html`에 링크했다.

## 하지 말 것

- router에 SQL이나 긴 업무 로직을 쓰지 않는다.
- service에 HTML/CSS를 넣지 않는다.
- 일반 CRUD를 `class/` 아래에 넣지 않는다. 다른 부위 코드를 `class/brain/`에 넣지 않는다.
- 비밀값(DB 비밀번호, `SECRET_KEY`, `HF_TOKEN`, NAS 계정)을 코드, 로그, 커밋, 채팅 응답에 적지 않는다. 설정은 `config.py`의 `Settings`와 `.env`로만 읽는다. `.env`는 커밋하지 않고, 키 이름이 바뀌면 `.env.example`만 갱신한다.
- 요청받지 않은 커밋·푸시·포맷 전체 수정·무관한 리팩터를 하지 않는다.
- 기존 파일의 문체를 유지한다. 독스트링·주석은 그 파일이 한국어이면 한국어로 쓴다.

## 확인

앱 진입점은 `main.py`다. 테스트는 `pytest`. 화면을 바꿨으면 해당 관리자 페이지에서 조회·저장이 되는지 확인한다.
