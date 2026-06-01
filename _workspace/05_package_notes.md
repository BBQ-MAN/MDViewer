# MDViewer 패키징 노트 (Phase 4)

> 작성: packager · 2026-06-01
> 게이트: 04_qa_report.md 종합 PASS (코어 단위 53/53, 경계면 일치, blocker 없음) 확인 후 진행.

## 1. 환경 / 버전

| 항목 | 값 |
|---|---|
| OS | Windows 11 (10.0.26200) |
| Python | 3.13.12 |
| PySide6 | 6.9.3 |
| PyInstaller | 6.20.0 (contrib hooks 2026.5) |
| 빌드 모드 | **onedir**, `console=False`(windowed) |

## 2. 빌드 명령 (재현)

```powershell
python -m pip install pyinstaller
python -m PyInstaller mdviewer.spec --noconfirm
# 산출물: dist\MDViewer\MDViewer.exe  (+ dist\MDViewer\_internal\)
```

## 3. mdviewer.spec 핵심

- `datas, binaries, hiddenimports = collect_all("PySide6")` — `--collect-all PySide6` 상당.
  Qt/WebEngine 의 datas·binaries·hiddenimports 를 일괄 수집(QtWebEngineProcess.exe, ICU,
  resources/*.pak, translations, platforms 플러그인 포함).
- 앱 리소스: `datas += [("src/mdviewer/resources", "mdviewer/resources")]`.
  → frozen 리소스 루트 = `<_MEIPASS>/mdviewer/resources`.
  paths.py 의 `_BUNDLE_RESOURCE_SUBPATH = "mdviewer/resources"` 와 **정합 확인**.
- `pathex=["src"]` (src 레이아웃 진입점 `src/mdviewer/__main__.py`).
- `console=False`, `name="MDViewer"`, `icon=None` (앱 .ico 자산이 없어 기본 아이콘 사용 — §6 제약).

## 4. WebEngine 번들 검증 (빌드 후 파일 존재 확인) — 모두 OK

청사진 §7 / QA §7 이 지적한 "빈 화면 주원인 = WebEngine 리소스 누락"을 dist 에서 실측:

| 항목 | 경로(상대) | 결과 |
|---|---|---|
| MDViewer.exe | `MDViewer.exe` | OK |
| WebEngine 보조 프로세스 | `_internal/PySide6/QtWebEngineProcess.exe` | OK |
| ICU 데이터 | `_internal/PySide6/resources/icudtl.dat` | OK |
| WebEngine 리소스 pak | `_internal/PySide6/resources/qtwebengine_resources.pak` (+devtools/100p/200p) | OK |
| 로케일 pak | `_internal/PySide6/translations/qtwebengine_locales/` (53개, ko.pak 포함) | OK |
| 플랫폼 플러그인 | `_internal/PySide6/plugins/platforms/qwindows.dll` | OK |
| 앱 CSS 리소스 | `_internal/mdviewer/resources/styles/github-{light,dark}.css` | OK |

## 5. 실제 실행 검증 (빌드 성공 ≠ 실행 성공)

### 5.0 ⚠️ 최초 빌드 크래시와 수정 (빌드 성공 ≠ 실행 성공의 실제 사례)
- **증상**: 최초 onedir 빌드의 `MDViewer.exe samples\demo.md` 가 **종료 코드 1로 조기 크래시**.
- **근본 원인**: PyInstaller 진입점이 `src/mdviewer/__main__.py` 였는데 그 안의 `from .app import main`
  이 **상대 import**다. frozen 상태에선 진입 스크립트가 패키지 컨텍스트 없이 `__main__` 으로 실행되어
  `ImportError: attempted relative import with no known parent package` 로 죽는다.
  (`python -m mdviewer` 는 -m 이 패키지 컨텍스트를 줘 정상이라 개발 모드에선 안 드러났다.)
- **수정**: 절대 import 전용 런처 `run_mdviewer.py`(`from mdviewer.app import main`) 추가,
  spec 의 `Analysis` 진입점을 `run_mdviewer.py` 로 변경(`pathex=["src"]` 유지). `__main__.py` 는 `-m` 용 보존.
- 재빌드 후 정상 실행 확인.

### 5.1 windowed exe 스모크
- `MDViewer.exe samples\demo.md` 를 **다른 작업 디렉터리(%TEMP%)** 에서 실행 →
  **10초 이상 정상 생존**(즉시 종료/크래시 없음). 수정 전엔 exit 1 로 즉사했으나 수정 후 생존 →
  WebEngine 정상 초기화 신호. (PASS)

### 5.2 console 프로브로 frozen 렌더 실측 (가장 확실) — PASS
- 임시 `_probe_render.py` + `_probe.spec`(console=True, 절대 import) 로 frozen exe 빌드 후 실행.
- 프로브 동작: frozen 상태에서 `renderer.read_markdown` → `renderer.render` →
  `theme.wrap_document` → `QWebEngineView.setHtml`, `loadFinished` 후 페이지 DOM 을 JavaScript 로 조회.
- **결과 (실측)**:
  - `render html len=6114, toc=18, title 추출됨`
  - `loadFinished ok=True` / DOM: **헤딩 18 · 코드블록(.codehilite) 4 · 테이블 1 · 본문 1387자**
  - → frozen exe 가 창만 띄우는 게 아니라 **실제로 마크다운을 렌더**함을 확인.
  - 헤드리스 검증 시 `QTWEBENGINE_CHROMIUM_FLAGS="--disable-gpu --in-process-gpu"` 사용.
- 프로브 임시물(`_probe_render.py`, `_probe.spec`, `dist/MDViewerProbe/`)은 검증 후 **삭제 완료**.

## 6. 알려진 제약

- **앱 아이콘 없음**: `src/mdviewer/resources/icons/` 에 `.ico` 자산이 없어 `icon=None`
  (PyInstaller 기본 아이콘). 브랜드 아이콘이 필요하면 멀티해상도(16~256px) `app.ico` 를
  추가하고 spec 의 `EXE(... icon=...)` 에 지정 후 재빌드.
- **onefile 미권장**: QWebEngine 앱은 onefile 시 매 실행 압축 해제로 시작이 느리고
  WebEngine 보조 프로세스/리소스 경로 문제로 런타임 크래시가 잦다. onedir 로 안정성 확보.
- **폴더 단위 배포 필수**: `dist\MDViewer\` 전체(특히 `_internal\`)를 함께 배포해야 한다.
  `MDViewer.exe` 단독 복사로는 동작하지 않는다.
- **SQL 드라이버 DLL 경고(무해)**: 빌드 로그에 fbclient/OCI/LIBPQ/MIMAPI64 미해결 경고가
  나오나, 이는 사용하지 않는 Qt SQL 드라이버의 선택적 의존성이며 앱 동작과 무관.
