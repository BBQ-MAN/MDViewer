#!/usr/bin/env bash
# MDViewer macOS 빌드 스크립트.
#
# ⚠️ 반드시 macOS 에서 실행한다 (PyInstaller 는 크로스 컴파일 불가).
#
# 사용법:
#   chmod +x build_macos.sh
#   ./build_macos.sh                # .app 빌드
#   ./build_macos.sh --dmg          # .app + 배포용 .dmg 생성
#
# 산출물: dist/MDViewer.app  (옵션: dist/MDViewer.dmg)

set -euo pipefail

cd "$(dirname "$0")"

PYTHON="${PYTHON:-python3}"
MAKE_DMG=0
[[ "${1:-}" == "--dmg" ]] && MAKE_DMG=1

echo "==> Python: $("$PYTHON" --version)"
echo "==> 아키텍처: $(uname -m)  (arm64=Apple Silicon, x86_64=Intel)"

# 1) 가상환경(권장) + 의존성
if [[ ! -d .venv ]]; then
  echo "==> .venv 생성"
  "$PYTHON" -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt pyinstaller

# 2) (선택) 단위 테스트 — 코어는 GUI 무의존이라 맥에서도 그대로 통과해야 함
if python -c "import pytest" 2>/dev/null; then
  echo "==> 단위 테스트"
  PYTHONPATH=src python -m pytest tests -q || { echo "테스트 실패 — 빌드 중단"; exit 1; }
fi

# 3) 빌드
echo "==> PyInstaller 빌드"
python -m PyInstaller mdviewer.spec --noconfirm --clean

APP="dist/MDViewer.app"
[[ -d "$APP" ]] || { echo "빌드 실패: $APP 없음"; exit 1; }
echo "==> 빌드 완료: $APP"

# 4) 실행 검증 (빌드 성공 ≠ 실행 성공) — 8초 생존 확인
echo "==> 실행 스모크(8초)"
open "$APP" --args "samples/demo.md" || true
sleep 8
if pgrep -f "MDViewer.app/Contents/MacOS/MDViewer" >/dev/null; then
  echo "   PASS: 앱 생존(크래시 없음). 종료합니다."
  pkill -f "MDViewer.app/Contents/MacOS/MDViewer" || true
else
  echo "   ⚠️ 앱이 조기 종료됨. 터미널에서 직접 실행해 로그 확인:"
  echo "      ./dist/MDViewer.app/Contents/MacOS/MDViewer samples/demo.md"
fi

# 5) 코드 서명(선택) — 환경변수 CODESIGN_ID 가 있으면 ad-hoc 대신 Developer ID 서명
#    Gatekeeper 배포에는 서명+공증 필요. 자세한 건 references/macos-packaging.md 참조.
if [[ -n "${CODESIGN_ID:-}" ]]; then
  echo "==> Developer ID 서명: $CODESIGN_ID"
  codesign --deep --force --options runtime --sign "$CODESIGN_ID" "$APP"
  codesign --verify --deep --strict --verbose=2 "$APP"
else
  echo "==> 서명 생략(미서명). 다른 맥에서 처음 열 때 우클릭 > 열기 필요."
  echo "   또는: xattr -dr com.apple.quarantine \"$APP\""
fi

# 6) DMG(선택)
if [[ "$MAKE_DMG" == "1" ]]; then
  echo "==> DMG 생성"
  DMG="dist/MDViewer.dmg"
  rm -f "$DMG"
  hdiutil create -volname "MDViewer" -srcfolder "$APP" -ov -format UDZO "$DMG"
  echo "   생성: $DMG"
fi

echo "==> 끝. 배포물: $APP"
