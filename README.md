# MDViewer

Python/PySide6 기반 크로스플랫폼(Windows·macOS) 데스크톱 마크다운 뷰어.

파일 열기, 실시간 렌더링(파일 변경 감시), 코드 하이라이트, 목차/내부 링크,
다크/라이트 테마, 최근 파일, 드래그앤드롭, 명령줄 인자 파일 열기를 지원한다.

- 코어 엔진(`renderer`, `file_watcher`)은 PySide6 무의존 — GUI 없이 단위 테스트 가능.
- UI 계층(`app`, `main_window`, `theme`, `settings`)은 PySide6 + QWebEngineView.

## 요구 사항

- Python 3.11 이상 (개발/빌드 검증 환경: Python 3.13.12)
- Windows 10/11 또는 macOS 11+ (Apple Silicon/Intel)

## 개발 실행

### 방법 A — editable 설치 (권장)

```powershell
python -m pip install -e .
python -m mdviewer                  # 웰컴 화면
python -m mdviewer samples\demo.md  # 파일 열기
```

editable 설치 시 콘솔 없는 GUI 진입점 `mdviewer` 와 콘솔 진입점 `mdviewer-cli` 도 생성된다.

### 방법 B — PYTHONPATH 사용 (설치 없이)

```powershell
python -m pip install -r requirements.txt   # 의존성만 설치
$env:PYTHONPATH = "src"
python -m mdviewer samples\demo.md
```

### 테스트

```powershell
$env:PYTHONUTF8 = "1"; $env:PYTHONPATH = "src"
python -m pytest tests -q
```

## 빌드 (Windows .exe)

PyInstaller 로 onedir 실행파일을 만든다. WebEngine 안정성을 위해 onedir 를 사용한다(onefile 미권장 — 아래 참고).

```powershell
python -m pip install pyinstaller
python -m PyInstaller mdviewer.spec --noconfirm
```

빌드 산출물:

```
dist\MDViewer\MDViewer.exe      # 더블클릭 실행 또는 파일 인자 전달
dist\MDViewer\_internal\        # Qt/WebEngine 런타임 + 앱 리소스(번들)
```

실행 예:

```powershell
dist\MDViewer\MDViewer.exe samples\demo.md
```

> 배포 시 `dist\MDViewer\` **폴더 전체**를 함께 배포해야 한다(`MDViewer.exe` 단독으로는 동작하지 않음).

### onefile 미권장 사유

QWebEngineView 앱은 onefile 로 빌드하면 매 실행마다 임시 폴더로 압축을 해제하므로
시작이 느리고, WebEngine 보조 프로세스/리소스 경로 문제로 런타임 크래시가 잦다.
안정적인 배포를 위해 onedir 를 기본으로 한다.

## 빌드 (macOS .app / .dmg)

> ⚠️ **PyInstaller 는 크로스 컴파일을 지원하지 않는다.** Windows 에서 macOS 앱을 만들 수 없다.
> 아래는 **맥(또는 macOS CI)에서** 실행한다.

### 방법 A — 맥에서 직접 빌드

```bash
chmod +x build_macos.sh
./build_macos.sh           # dist/MDViewer.app 생성 + 실행 스모크
./build_macos.sh --dmg     # 추가로 dist/MDViewer.dmg 생성
```

또는 수동으로:

```bash
python3 -m pip install -r requirements.txt pyinstaller
python3 -m PyInstaller mdviewer.spec --noconfirm
open dist/MDViewer.app
```

같은 `mdviewer.spec` 이 `sys.platform` 으로 분기해 macOS 에선 `.app` 번들(Info.plist·다크모드·`.md` 문서 타입 연결 포함)을 생성한다. macOS 는 Finder 더블클릭 시 경로를 `argv` 가 아닌 Apple Event 로 전달하므로, 앱이 `QFileOpenEvent` 를 처리한다(구현됨).

### 방법 B — 맥이 없을 때 (GitHub Actions)

저장소의 `.github/workflows/build-macos.yml` 가 macOS 러너에서 빌드해 `.dmg` 를 아티팩트로 올린다. GitHub 의 Actions 탭에서 **Run workflow** 로 수동 실행하거나 `v*` 태그를 push 한다.

### 아키텍처 / 배포 주의

- 기본 빌드는 **빌드한 맥의 아키텍처**(Apple Silicon→arm64, Intel→x86_64)만 지원한다. 둘 다 지원하려면 각 아키텍처에서 빌드하거나 universal2 를 구성한다.
- **미서명** 빌드는 받는 사람이 처음 열 때 우클릭 ▸ 열기, 또는 `xattr -dr com.apple.quarantine MDViewer.app` 가 필요하다. 경고 없는 배포는 Developer ID 서명 + 공증이 필요하다(`$99/년`).
- 서명·공증·DMG 상세는 빌드 스킬의 `references/macos-packaging.md` 참조.

## 라이선스

MIT. PySide6 는 LGPL.
