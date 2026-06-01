# macOS 패키징 — MDViewer .app/.dmg

> PySide6 + QWebEngine 앱을 macOS `.app` 번들로 패키징하고 배포하는 방법.
> Windows 패키징은 SKILL.md 본문 참조. 이 문서는 macOS 빌드 요청 시에만 로드한다.

## 목차
1. 절대 원칙: 크로스 컴파일 불가
2. 빌드 환경
3. spec 의 macOS 분기 (BUNDLE/Info.plist)
4. 파일 연결(.md 더블클릭) — QFileOpenEvent
5. 아키텍처: arm64 / x86_64 / universal2
6. 코드 서명 & 공증 (배포 단계별)
7. DMG 생성
8. 맥이 없을 때: GitHub Actions
9. 흔한 실패와 대응

## 1. 절대 원칙: 크로스 컴파일 불가
PyInstaller 는 실행 중인 OS 용 산출물만 만든다. **Windows 에서 .app 을 만들 수 없고, macOS 에서 .exe 를 만들 수 없다.** macOS 빌드는 반드시 (a) 실제 맥, (b) macOS VM, (c) macOS CI 러너 중 하나에서 수행한다. 이걸 모르고 Windows 에서 시도하는 것이 가장 흔한 오해다.

## 2. 빌드 환경
- macOS 11+ 권장, Python 3.12(또는 3.11) — PySide6 6.8+ 와 안정 조합. (3.13 도 6.8+면 동작하나 CI 는 보수적으로 3.12 권장.)
- 가상환경 + `pip install -r requirements.txt pyinstaller`.
- 빌드 스크립트: 저장소 루트의 `build_macos.sh` (`./build_macos.sh` 또는 `--dmg`).

## 3. spec 의 macOS 분기
`mdviewer.spec` 은 `sys.platform == "darwin"` 으로 분기한다:
- `collect_all("PySide6")` 는 Windows 와 동일하게 Qt/WebEngine 일괄 수집.
- onedir `COLLECT` 까지는 공통, macOS 만 끝에 `BUNDLE(...)` 로 `.app` 생성.
- `BUNDLE` 의 `info_plist` 에 다음을 넣는다:
  - `bundle_identifier`: 역DNS (예: `kr.lectus.mdviewer`) — 서명/공증/설정 저장소 식별에 사용.
  - `NSHighResolutionCapable: True` — 레티나 선명도.
  - `NSRequiresAquaSystemAppearance: False` — 시스템 다크모드 추종 허용.
  - `LSMinimumSystemVersion`.
  - `CFBundleDocumentTypes` — `.md/.markdown/...` 를 이 앱이 여는 문서 타입으로 등록(Finder '연결 프로그램').
- `EXE(argv_emulation=True)` (macOS) — Finder 더블클릭 시 경로 전달 보조. 단, 신뢰 가능한 경로는 QFileOpenEvent(§4)다.

## 4. 파일 연결(.md 더블클릭) — QFileOpenEvent
macOS 는 Finder 더블클릭/Dock 드롭 시 파일 경로를 `argv` 로 주지 않고 **Apple Event → `QEvent.Type.FileOpen`** 으로 보낸다. `argv` 만 읽는 앱은 맥에서 파일 연결이 안 된다.
→ `app.py` 의 `MDViewerApplication(QApplication)` 이 `event()` 에서 `FileOpen` 을 처리한다. 이벤트가 윈도우 생성 전 도착할 수 있어 버퍼링 후 `set_main_window` 에서 플러시한다. Windows/Linux 에선 이 이벤트가 안 떠서 무해하다. **이 핸들러가 없으면 .app 의 문서 타입 등록이 무용지물**이므로 반드시 유지한다.

## 5. 아키텍처: arm64 / x86_64 / universal2
- `target_arch=None`(기본): **빌드 머신 아키텍처**로 생성. Apple Silicon 맥 → arm64, Intel 맥 → x86_64.
- arm64 .app 은 Intel 맥에서 안 돈다(그 반대도). 둘 다 지원하려면:
  - 각 아키텍처 맥(또는 CI 잡)에서 각각 빌드, 또는
  - `target_arch="universal2"` — 단, **모든 의존성 휠이 universal2 여야** 한다. PySide6 는 보통 아키텍처별 휠이라 universal2 빌드가 까다롭다. 현실적으로는 arm64/x86_64 **별도 빌드 2개 배포**가 안전하다.
- 확인: `file dist/MDViewer.app/Contents/MacOS/MDViewer` 로 아키텍처 출력.

## 6. 코드 서명 & 공증 (배포 단계별)
배포 범위에 따라 필요 수준이 다르다. 위로 갈수록 간단, 아래로 갈수록 배포 친화적.

| 단계 | 무엇 | 받는 사람 경험 | 필요물 |
|------|------|---------------|--------|
| A. 미서명 | 아무것도 안 함 | 첫 실행 시 "확인되지 않은 개발자" 차단 → 우클릭>열기 또는 `xattr -dr com.apple.quarantine MDViewer.app` | 없음 |
| B. Ad-hoc 서명 | `codesign -s -` | 본인 맥에선 무난, 타 맥은 A 와 유사 | 없음 |
| C. Developer ID 서명 | `codesign --options runtime --sign "Developer ID Application: ..."` | 다운로드 후 경고 줄지만 공증 없으면 여전히 차단 가능 | Apple Developer 계정($99/년) + 인증서 |
| D. 서명 + 공증 + 스테이플 | C + `notarytool submit` + `stapler staple` | 경고 없이 바로 실행(권장 배포) | C + 공증 |

**D(권장 배포) 절차:**
```bash
# 1) 하드닝드 런타임으로 서명
codesign --deep --force --options runtime \
  --sign "Developer ID Application: NAME (TEAMID)" dist/MDViewer.app
# 2) zip 으로 묶어 공증 제출
ditto -c -k --keepParent dist/MDViewer.app dist/MDViewer.zip
xcrun notarytool submit dist/MDViewer.zip \
  --apple-id "you@apple.id" --team-id TEAMID --password "앱암호" --wait
# 3) 티켓 스테이플(오프라인에서도 통과)
xcrun stapler staple dist/MDViewer.app
spctl -a -vv dist/MDViewer.app   # accepted 확인
```
> WebEngine 앱은 보조 실행파일(QtWebEngineProcess)이 많아 `--deep` 또는 내부 바이너리 개별 서명이 필요할 수 있다. 공증 실패 시 `notarytool log` 로 어느 바이너리가 미서명/미하드닝인지 확인한다.

## 7. DMG 생성
```bash
hdiutil create -volname "MDViewer" -srcfolder dist/MDViewer.app -ov -format UDZO dist/MDViewer.dmg
```
배포용은 보통 **서명·공증된 .app → DMG → (DMG 자체도 서명·공증)** 순. 단순 전달은 .app 압축(zip)도 충분.

## 8. 맥이 없을 때: GitHub Actions
저장소 `.github/workflows/build-macos.yml` 가 `macos-14`(arm64) 러너에서 빌드→DMG→아티팩트 업로드한다. Actions 탭에서 수동 실행하거나 `v*` 태그 push 로 트리거. 공개 저장소는 무료, 비공개는 분 단위 한도. 서명/공증을 CI 에 넣으려면 인증서와 앱 암호를 Secrets 로 저장하고 §6 D 단계를 잡에 추가한다.

## 9. 흔한 실패와 대응
| 증상 | 원인 | 대응 |
|------|------|------|
| Windows 에서 .app 빌드 시도 실패 | 크로스 컴파일 불가 | 맥/CI 에서 빌드 |
| .app 더블클릭 시 "손상됨/확인 불가" | quarantine + 미서명 | 우클릭>열기, 또는 `xattr -dr com.apple.quarantine`, 또는 공증(§6 D) |
| .md 더블클릭해도 안 열림 | QFileOpenEvent 미처리 또는 문서 타입 미등록 | app.py 핸들러(§4) + Info.plist CFBundleDocumentTypes 확인, `lsregister` 재등록 |
| 다른 맥에서 실행 안 됨 | 아키텍처 불일치(arm64↔x86_64) | 대상 아키텍처에서 빌드(§5) |
| 빈 화면(렌더 안 됨) | WebEngine 리소스 누락 | `collect_all("PySide6")` 확인, `.app/Contents/Resources` 또는 `Frameworks` 에 WebEngine 리소스 존재 확인 |
| 공증 거부 | 미하드닝/미서명 내부 바이너리 | `--options runtime` 전체 서명, `notarytool log` 로 원인 바이너리 추적 |
