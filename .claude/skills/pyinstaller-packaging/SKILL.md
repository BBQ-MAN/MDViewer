---
name: pyinstaller-packaging
description: PySide6 + QWebEngineView 앱(MDViewer)을 PyInstaller로 데스크톱 실행파일로 패키징하는 스킬. Windows(.exe) 및 macOS(.app/.dmg) 빌드, .spec 작성, Qt/WebEngine 리소스·플러그인 번들링, sys._MEIPASS 리소스 경로, 아이콘(.ico/.icns), onedir/onefile, 코드 서명·공증, 빌드 검증을 다룬다. MDViewer 배포 빌드 시 반드시 사용. "패키징", "exe", "맥/macOS 빌드", ".app", "dmg", "PyInstaller", "빌드", "배포", "서명/공증" 작업에 적용.
---

# PyInstaller Packaging — MDViewer 데스크톱 빌드

검증된 소스를 **더블클릭 실행 가능한 배포물**로 만든다(Windows .exe / macOS .app). PySide6 + QWebEngine 패키징의 함정은 "빌드는 성공, 실행은 크래시"다. 핵심 가치는 그 함정을 피하고 **실제 실행을 검증**하는 것.

> **macOS(.app/.dmg) 빌드 요청 시 → `references/macos-packaging.md` 를 읽고 따른다.** 그 문서에 크로스컴파일 제약, BUNDLE/Info.plist, 파일연결(QFileOpenEvent), 아키텍처(arm64/x86_64/universal2), 코드서명·공증, DMG, GitHub Actions, 실패 대응이 모두 있다. **PyInstaller 는 크로스 컴파일 불가** — Windows 에서 .app 을, macOS 에서 .exe 를 만들 수 없으니 각 OS(또는 macOS CI)에서 빌드한다. 아래 본문은 Windows 빌드 기준이며 collect_all·리소스 경로·실행검증 원칙은 양 플랫폼 공통이다.

## 1. 빌드 전 게이트

`_workspace/04_qa_report.md`가 PASS인지 확인한다. 미통과 상태로 패키징하면 결함을 그대로 배포한다. FAIL이 남아 있으면 packager는 멈추고 리더에 보고한다.

## 2. WebEngine 번들링 — 가장 중요

QWebEngineView는 `QtWebEngineProcess.exe`, ICU 데이터, `resources/`, `translations/`, 플랫폼 플러그인을 런타임에 필요로 한다. 누락되면 창은 뜨지만 렌더가 안 되거나 즉시 종료된다. PySide6는 보통 hook이 대부분 처리하지만 명시적으로 보강한다:

```python
# mdviewer.spec (핵심 부분)
from PyInstaller.utils.hooks import collect_all
datas, binaries, hiddenimports = collect_all("PySide6")

a = Analysis(
    ["src/mdviewer/__main__.py"],
    pathex=["src"],
    binaries=binaries,
    datas=datas + [("src/mdviewer/resources", "mdviewer/resources")],  # 앱 리소스
    hiddenimports=hiddenimports,
)
```

또는 CLI로: `pyinstaller --collect-all PySide6 --add-data "src/mdviewer/resources;mdviewer/resources" ...` (Windows는 `;` 구분자).

## 3. 앱 리소스 경로 — sys._MEIPASS

코드가 architect의 `paths.resource_dir()`를 쓰고 있어야 번들에서 CSS/아이콘이 로드된다. 안 되어 있으면 ui-dev에게 수정 요청:

```python
def resource_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "mdviewer" / "resources"
    return Path(__file__).parent / "resources"
```

`--add-data`의 대상 경로(`mdviewer/resources`)와 `resource_dir()`의 조합 경로가 정확히 일치해야 한다 — 불일치가 "번들에서만 깨짐"의 단골 원인.

## 4. onedir vs onefile

| 모드 | 장점 | 단점 | 권장 |
|------|------|------|------|
| **onedir** (기본) | 시작 빠름, WebEngine 안정, 디버깅 쉬움 | 폴더 배포 | ✅ 기본값 |
| **onefile** | 단일 파일 배포 | 시작 느림(압축 해제), WebEngine 문제 잦음 | 검증 후 옵션 |

WebEngine 앱은 onedir로 안정성을 먼저 확보한다. onefile은 onedir 검증 통과 후 별도 제공.

## 5. 윈도우/아이콘/메타데이터

```python
exe = EXE(
    ...,
    name="MDViewer",
    console=False,          # --windowed, 콘솔 창 숨김
    icon="src/mdviewer/resources/icons/app.ico",
)
```

`.ico`는 멀티 해상도(16~256px) 권장. 콘솔 없는 GUI 앱이므로 `console=False`.

## 6. 빌드 & 검증

```powershell
pip install pyinstaller
pyinstaller mdviewer.spec --noconfirm
# 산출물: dist/MDViewer/MDViewer.exe (onedir)
```

**빌드 성공 ≠ 실행 성공.** 반드시 실제 실행 검증:
```powershell
# 빌드 폴더가 아닌 곳에서 실행해 상대경로 의존을 노출
dist/MDViewer/MDViewer.exe samples/demo.md
```
확인: 창이 뜨는가 → 마크다운이 렌더되는가 → 코드 하이라이트·이미지·테마가 정상인가 → 콘솔 에러 없이 종료되는가. WebEngine 누락은 여기서만 드러난다.

## 7. 흔한 실패와 대응

| 증상 | 원인 | 대응 |
|------|------|------|
| 창은 뜨나 렌더 빈 화면 | WebEngine 리소스 누락 | `--collect-all PySide6` 확인, QtWebEngineProcess 포함 확인 |
| CSS/아이콘 안 보임 | base path 불일치 | `resource_dir()` ↔ `--add-data` 경로 정합 |
| 즉시 종료(콘솔 깜빡) | 플랫폼 플러그인 누락 | `console=True`로 임시 빌드해 트레이스백 확인 |
| 시작 매우 느림 | onefile 압축 해제 | onedir로 전환 |

## 8. 산출물

`mdviewer.spec`, 빌드 명령 문서, `dist/`의 실행파일. `_workspace/05_package_notes.md`에 빌드 명령·환경(Python/PySide6 버전)·실행 검증 결과·알려진 제약(onefile 미권장 사유 등)을 기록해 재현 가능하게 한다.
