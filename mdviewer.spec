# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for MDViewer (PySide6 + QWebEngineView) — 크로스플랫폼.

빌드:
    pyinstaller mdviewer.spec --noconfirm

산출물:
    Windows : dist/MDViewer/MDViewer.exe        (onedir — WebEngine 안정성)
    macOS   : dist/MDViewer.app                 (BUNDLE — Finder/Dock 통합)

⚠️ PyInstaller 는 크로스 컴파일을 지원하지 않는다.
   Windows 에서 .app 을, macOS 에서 .exe 를 만들 수 없다. 각 OS 에서 빌드해야 한다.

핵심:
    - collect_all("PySide6") 로 Qt/WebEngine datas·binaries·hiddenimports 일괄 수집
      (QtWebEngineProcess, ICU dat, resources/*.pak, translations, 플랫폼 플러그인 포함).
    - 앱 리소스를 'mdviewer/resources' 로 수집 → paths.py 의
      _BUNDLE_RESOURCE_SUBPATH("mdviewer/resources") 와 정합.
    - 진입점은 절대 import 런처 run_mdviewer.py (frozen 상대 import 크래시 회피).
    - onedir + console=False(windowed). macOS 는 추가로 BUNDLE 로 .app 생성.
"""

import os
import sys

from PyInstaller.utils.hooks import collect_all

IS_MAC = sys.platform == "darwin"
IS_WIN = sys.platform == "win32"

# Qt/WebEngine 전체 수집 (--collect-all PySide6 상당)
datas, binaries, hiddenimports = collect_all("PySide6")

# --- Word(.docx) 내보내기 의존성 수집 (Phase 10, 설계 §7.1) ----------------
# exporter.py 가 markdown_to_docx 내부에서 `from docx import Document` 등을
# **지연 import** 하므로 PyInstaller 정적 분석이 놓칠 수 있다. 명시 수집 필수.
#
# ⚠️ collect_all 의 인자는 *import 이름*이다. python-docx 의 import 이름은 'docx'
#    (배포명 python-docx 와 다름). collect_all("docx") 가 맞다.
#
# collect_all("docx") 는 빈 문서 템플릿(templates/default.docx,
# templates/default-docx-template/*, default-*.xml 스타일)을 datas 로,
# 모듈들을 hiddenimports 로 수집한다. 누락 시 frozen 에서 PackageNotFoundError /
# "템플릿 못 찾음" 으로 .docx 생성이 실패한다.
_docx_datas, _docx_bins, _docx_hidden = collect_all("docx")
datas += _docx_datas
binaries += _docx_bins
hiddenimports += _docx_hidden

# lxml 네이티브(C 확장 .pyd = libxml2 바인딩). python-docx 의 트랜지티브 의존.
# PyInstaller 기본 훅이 대개 처리하나, 명시 수집으로 etree*.pyd 번들을 보장한다.
_lxml_datas, _lxml_bins, _lxml_hidden = collect_all("lxml")
datas += _lxml_datas
binaries += _lxml_bins
hiddenimports += _lxml_hidden
# --------------------------------------------------------------------------

# 앱 리소스(styles/ 등). 대상 경로는 paths._BUNDLE_RESOURCE_SUBPATH 와 반드시 일치.
datas += [("src/mdviewer/resources", "mdviewer/resources")]

# 아이콘(선택) — 플랫폼별 포맷. 파일이 있을 때만 사용, 없으면 기본 아이콘.
_ICON = None
if IS_WIN and os.path.exists("src/mdviewer/resources/icons/app.ico"):
    _ICON = "src/mdviewer/resources/icons/app.ico"
elif IS_MAC and os.path.exists("src/mdviewer/resources/icons/app.icns"):
    _ICON = "src/mdviewer/resources/icons/app.icns"


a = Analysis(
    ["run_mdviewer.py"],
    pathex=["src"],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="MDViewer",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,          # windowed: 콘솔 창 숨김
    disable_windowed_traceback=False,
    argv_emulation=IS_MAC,  # macOS: Finder 더블클릭 시 파일 경로 전달 보조(FileOpen 이벤트와 병행)
    target_arch=None,       # None = 빌드 머신 아키텍처(Apple Silicon→arm64). universal2 는 references 참조.
    codesign_identity=None,
    entitlements_file=None,
    icon=_ICON,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="MDViewer",
)

# macOS 전용: .app 번들 생성 (Finder/Dock 통합, .md 문서 타입 연결, 다크모드 허용)
if IS_MAC:
    app = BUNDLE(
        coll,
        name="MDViewer.app",
        icon=_ICON,
        bundle_identifier="kr.lectus.mdviewer",
        info_plist={
            "CFBundleName": "MDViewer",
            "CFBundleDisplayName": "MDViewer",
            "CFBundleShortVersionString": "0.1.0",
            "CFBundleVersion": "0.1.0",
            "NSHighResolutionCapable": True,
            # False 여야 macOS 시스템 다크모드를 따라간다.
            "NSRequiresAquaSystemAppearance": False,
            "LSMinimumSystemVersion": "11.0",
            "CFBundleDocumentTypes": [
                {
                    "CFBundleTypeName": "Markdown Document",
                    "CFBundleTypeRole": "Viewer",
                    "LSHandlerRank": "Alternate",
                    "CFBundleTypeExtensions": ["md", "markdown", "mdown", "mkd"],
                    "LSItemContentTypes": ["net.daringfireball.markdown"],
                }
            ],
        },
    )
