---
name: desktop-app-qa
description: PySide6 데스크톱 앱(MDViewer)을 실제 실행해 검증하고, 코어 엔진과 UI의 경계면(렌더 API 계약)을 교차 비교해 통합 버그를 잡는 QA 스킬. 스모크 테스트, 코어 단위 테스트, 경계면 shape 대조, 렌더 정확성·견고성 검증을 다룬다. MDViewer 품질 검증 시 반드시 사용. "QA", "검증", "테스트", "통합 버그", "경계면" 작업에 적용.
---

# Desktop App QA — MDViewer 검증

"파일이 존재한다"가 아니라 **"기능이 작동한다"**를 확인한다. 핵심은 core 엔진과 UI를 잇는 **경계면 교차 비교**다. 각 모듈 완성 직후 점진적으로 검증한다.

## 1. 검증 우선순위 (위에서부터)

1. **경계면 교차 비교** — 가장 가치 높음. UI 호출부와 엔진 시그니처를 나란히 놓고 대조.
2. **코어 단위 테스트** — GUI 없이 자동 검증 가능. 가장 확실.
3. **앱 스모크 실행** — 크래시 없이 뜨고 렌더되는지.
4. **렌더 정확성·견고성** — 엣지 입력에 대한 동작.

## 2. 경계면 교차 비교 (핵심 기법)

단일 모듈이 완벽해도 두 모듈을 잇는 지점에서 깨진다. **두 파일을 동시에 읽어** shape을 대조한다:

- `renderer.py`의 실제 공개 시그니처 (`render(text, base_dir) -> RenderResult`, `RenderResult.html/toc/title`)
- `main_window.py`가 호출하는 방식 (인자 개수·이름·키워드, 반환값 사용 — `result.html`? `result["html"]`?)

대조 체크리스트:
- [ ] 함수명·인자 개수·인자 이름 일치
- [ ] 반환 타입 일치 (dataclass 속성 접근 vs dict 키 접근 불일치는 흔한 버그)
- [ ] 예외 정책 일치 (엔진이 던지는 예외를 UI가 잡는가)
- [ ] 파일 감시 인터페이스 일치 (콜백 시그니처 vs Signal 이름)
- [ ] 스레드 안전성 (watchdog 워커 → UI 스레드 전달이 시그널로 되어 있는가)

불일치를 발견하면 어느 쪽을 고칠지 책임 에이전트를 지정해 보고한다.

## 3. 코어 단위 테스트 (자동·확실)

코어는 PySide6 의존이 없으므로 헤드리스로 빠르게 검증된다. `tests/`에 작성하고 `python -m pytest`로 실행.

```python
def test_render_basic():
    r = render("# Hello\n\n`code`", base_dir=Path("."))
    assert "Hello" in r.html
    assert r.title == "Hello"
    assert r.toc[0].anchor   # 앵커 생성됨

def test_render_empty():          # 빈 입력 크래시 없음
    assert render("", base_dir=Path(".")).html is not None

def test_render_broken():         # 깨진 마크다운 크래시 없음
    render("```\nunclosed", base_dir=Path("."))

def test_code_highlight():
    r = render("```python\nprint(1)\n```", base_dir=Path("."))
    assert "codehilite" in r.html

def test_read_encoding(tmp_path): # 인코딩 감지
    p = tmp_path / "a.md"; p.write_text("한글", encoding="utf-8")
    assert "한글" in read_markdown(p)
```

엣지 입력(빈/깨진/바이너리/대용량/유니코드)을 반드시 포함한다 — 사용자는 임의 파일을 연다.

## 4. 앱 스모크 실행

GUI는 헤드리스 검증이 어렵다. 다음 단계로 판단한다:

```powershell
# import 스모크 — 모듈 로드 시 즉시 터지는 오류 포착
python -c "import mdviewer.app, mdviewer.main_window, mdviewer.renderer; print('import OK')"

# 실제 실행 — 샘플 파일로 띄우고 예외 유무·종료코드 확인
python -m mdviewer samples/demo.md
```

Windows에서 PySide6/QWebEngine은 플랫폼 플러그인·WebEngine 프로세스 문제가 실행 시에만 드러난다. import는 통과해도 `QApplication` 생성이나 `QWebEngineView` 초기화에서 터질 수 있으니 실제 실행 로그를 본다. 비대화식 환경이면 짧게 띄웠다 종료하는 방식이나 예외 캡처로 확인한다.

## 5. 렌더 정확성 검증 항목

`samples/demo.md`에 다음을 모두 담아 육안·자동으로 확인:
- 헤딩 계층과 목차 앵커(내부 링크 클릭 동작)
- 펜스 코드블록 구문 강조(여러 언어)
- 테이블, 각주, 작업 목록 체크박스
- 상대 경로 이미지 표시 (baseUrl 동작)
- 다크/라이트 테마 전환 시 코드 하이라이트 색 동기화

## 6. 리포트 작성

`_workspace/04_qa_report.md`에 항목별 PASS/FAIL, 경계면 불일치, 재현 절차, 담당 에이전트를 기록한다. FAIL은 책임 에이전트(core-engine-dev/ui-dev)에게 즉시 `SendMessage`. 1회 재시도 후에도 남으면 미해결로 명시하고 리더에 보고.

## QA 원칙

- **실행이 진실**: "맞아 보인다"로 끝내지 마라. import하고, 호출하고, 띄워라.
- **점진적**: 모듈 완성 즉시 검증. 늦게 잡은 경계면 버그가 가장 비싸다.
- **정직**: 실패는 출력과 함께 그대로 보고. 통과로 포장 금지.
- **packager 게이트**: QA PASS 전엔 패키징하지 않는다 — 결함을 그대로 배포하게 된다.
