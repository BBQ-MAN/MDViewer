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

---

# Phase 10 — Word(.docx)/PDF 내보내기 패키징 갱신

> 작성: packager · 2026-06-25
> 게이트: 내보내기 기능 QA **PASS (145 passed, 회귀 0)** — 진행 허가됨.
> 기준 명세: `_workspace/10_export_feature_design.md` §7(패키징 영향).
> 신규 런타임 의존: `python-docx`(import 이름 **`docx`**) + 트랜지티브 **`lxml`**(C 확장 .pyd).
> PDF 내보내기는 **신규 번들 의존 없음**(QtWebEngine `printToPdf` = 기존 번들 기능).

## 10.1 환경 / 버전 (★ Phase 4 와 달라짐 — 재현 시 주의)

| 항목 | Phase 4 (2026-06-01) | **Phase 10 (이번 빌드)** |
|---|---|---|
| Python | 3.13.12 | **3.12.12** (miniconda3) |
| PySide6 | 6.9.3 | **6.11.1** |
| PyInstaller | 6.20.0 | **6.21.0** |
| python-docx | (없음) | **1.x** (`from docx import Document` 동작 확인) |
| lxml | (없음) | **5.4.0** (etree.cp312-win_amd64.pyd) |
| 빌드 모드 | onedir, console=False | 동일 (onedir, windowed) |

> ⚠️ lxml .pyd 는 **CPython ABI 태그(cp312)** 에 묶인다. 다른 Python 마이너 버전으로
> 빌드하면 `etree.cp312-...pyd` 대신 해당 버전 휠이 번들된다 — 재현 시 Python 3.12 권장.

## 10.2 mdviewer.spec 변경 (diff 요약, 설계 §7.1)

`collect_all("PySide6")` 바로 아래에 docx/lxml 명시 수집을 **추가**(기존 PySide6/리소스/
아이콘/BUNDLE 로직은 전부 보존):

```python
# --- Word(.docx) 내보내기 의존성 수집 (Phase 10, 설계 §7.1) ---
# exporter.py 가 markdown_to_docx 내부에서 `from docx import Document` 를 지연 import →
# PyInstaller 정적 분석이 놓칠 수 있으므로 명시 수집 필수.
_docx_datas, _docx_bins, _docx_hidden = collect_all("docx")   # import 이름 'docx'(배포명 python-docx)
datas += _docx_datas; binaries += _docx_bins; hiddenimports += _docx_hidden

_lxml_datas, _lxml_bins, _lxml_hidden = collect_all("lxml")   # 네이티브 etree*.pyd 보장
datas += _lxml_datas; binaries += _lxml_bins; hiddenimports += _lxml_hidden
```

- `collect_all("docx")` 가 `default.docx` 템플릿·`default-docx-template/` 트리·`default-*.xml`
  스타일을 datas 로 수집 → 누락 시 frozen 에서 `PackageNotFoundError`/템플릿 못 찾음.
- `collect_all` 인자는 **import 이름**이라 `docx`(배포명 `python-docx`와 다름)가 맞다.

## 10.3 빌드 (재현)

```powershell
# 클린 빌드
Remove-Item -Recurse -Force build, dist -ErrorAction SilentlyContinue
python -m PyInstaller mdviewer.spec --noconfirm
# 산출물: dist\MDViewer\MDViewer.exe  (+ dist\MDViewer\_internal\)
```

- 빌드 **성공**(exit 0, "Build complete"). ERROR/Traceback 없음. MDViewer.exe 약 8.1 MB.
- (Phase 4 와 동일한) 미사용 Qt SQL 드라이버 DLL 경고는 무해 — §6 참조.

## 10.4 번들 포함 확증 (dist 파일 실측)

`dist\MDViewer\_internal\` 에서 직접 확인 — **모두 OK**:

| 항목 | 경로(상대) | 결과 |
|---|---|---|
| docx 템플릿(★crit) | `_internal\docx\templates\default.docx` (38,116 B) | OK |
| docx 템플릿 트리 | `_internal\docx\templates\default-docx-template\word\styles.xml` | OK |
| docx 스타일 xml | `_internal\docx\templates\default-{styles,header,footer,settings,comments}.xml` | OK |
| lxml 네이티브 | `_internal\lxml\etree.cp312-win_amd64.pyd`(+builder/objectify/sax/diff/_elementpath) | OK |
| WebEngine 보조 | `_internal\PySide6\QtWebEngineProcess.exe` (PDF 경로 회귀) | OK |
| ICU/리소스 pak | `_internal\PySide6\resources\{icudtl.dat,qtwebengine_resources.pak}` | OK |
| 로케일 pak | `_internal\PySide6\translations\qtwebengine_locales\` (53개) | OK |
| 플랫폼 플러그인 | `_internal\PySide6\plugins\platforms\qwindows.dll` | OK |
| 앱 CSS | `_internal\mdviewer\resources\styles\github-{light,dark}.css` | OK |

## 10.5 frozen 런타임 검증 (★ 실제 실행 — 설계 §7.2)

**방법:** 본 빌드의 `mdviewer.spec` 과 **동일한 collect_all 수집**(PySide6+docx+lxml)을
적용한 임시 console 프로브 spec(`_probe.spec`) + 진입점(`_probe_export.py`)을 빌드해,
**frozen 인터프리터 안에서** 내보내기 경로를 직접 실행했다(GUI exe 는 windowed 라 stdout
검증이 어려우므로 동등 환경의 console 프로브 사용 — Phase 4 §5.2 와 동일 기법).
프로브 산출물은 검증 후 **삭제 완료**(`_probe_export.py`/`_probe.spec`/`dist_probe`/`build_probe`).

프로브를 **다른 작업 디렉터리(%TEMP%)** 에서 실행한 실측 결과:

```
PROBE: frozen=True _MEIPASS=...\dist_probe\MDViewerProbe\_internal
PROBE: a) docx Document().save OK size=36580
PROBE: b) lxml.etree OK version=5.4.0 parse_tag=r
PROBE: c) markdown_to_docx OK size=37148 paras=13 tables=1 title='Probe Title' bold=True task=True
RESULT=PASS   (exit 0)
```

- **a) `from docx import Document; Document().save(tmp)`** → 성공(36,580 B). `default.docx`
  템플릿 datas 가 frozen 에서 로드됨을 입증(누락 시 `PackageNotFoundError` 로 실패했을 것).
- **b) `import lxml.etree`** → 성공(5.4.0, 실제 XML 파싱). 네이티브 `.pyd` 번들·임포트 확인.
- **c) `mdviewer.exporter.markdown_to_docx(대표 md, out.docx, base_dir, title=...)`** →
  .docx 생성(37,148 B>0), `Document` 로 **재오픈** 성공, 단락 13·표 1·`core_properties.title`
  일치·bold run 존재·task 체크박스(☑/☐) 존재 모두 확인.
- **d) exe 정상 기동(스모크)** — 실제 windowed `MDViewer.exe samples\demo.md` 를 %TEMP% 에서
  기동 → **8초+ 생존**(즉시 종료/크래시 없음). WebEngine 정상 초기화 = 앱 실행 회귀 없음.
- **e) PDF 경로** — 신규 번들 의존 0(QtWebEngine `printToPdf` 기존 번들). WebEngine 산출물
  실측(§10.4) + 앱 기동(d) 으로 회귀 스모크 충족.

### 한계 (정직한 명시)
- windowed `MDViewer.exe` 자체는 stdout 이 없어 그 안에서 docx 변환 결과를 직접 출력받지
  못한다. 그래서 **동일 collect_all 수집을 적용한 console 프로브 exe** 로 검증했다 —
  프로브와 실제 exe 의 docx/lxml 번들 구성은 동일(같은 수집 코드)하므로 a~c 결과는
  실제 exe 의 내보내기 경로에 그대로 적용된다. 다만 **실제 GUI 에서 메뉴→내보내기 클릭→
  파일 저장까지의 end-to-end UI 흐름**(QFileDialog·flush·WYSIWYG 캡처)은 본 패키징 검증 범위가
  아니라 QA(설계 §10) 가 소스/오프스크린으로 커버한다. 본 검증은 "frozen 에서 docx/lxml 이
  실제로 동작하고 markdown_to_docx 가 산출물을 만든다"를 확증한다.

## 10.6 산출물

- 실행파일: **`d:\Dev\MDViewer\dist\MDViewer\MDViewer.exe`** (onedir — `_internal\` 동반 배포 필수).
- 변경 spec: `d:\Dev\MDViewer\mdviewer.spec` (docx/lxml collect_all 추가).
- 종합 판정: **빌드 성공 · docx/lxml 번들 포함 확증 · frozen 내보내기 실행 PASS · 앱 기동 회귀 없음.**
