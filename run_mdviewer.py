"""Frozen 진입점 전용 런처 — 절대 import 사용.

PyInstaller frozen 상태에서 진입 스크립트는 패키지 컨텍스트 없이 `__main__` 으로
실행되므로 `__main__.py` 의 상대 import(`from .app import main`)가
`ImportError: attempted relative import with no known parent package` 로 죽는다.
이 런처는 절대 import 로 `mdviewer` 패키지를 불러와 그 함정을 피한다.
(`python -m mdviewer` 용도로는 기존 `src/mdviewer/__main__.py` 를 그대로 보존한다.)
"""

import sys

from mdviewer.app import main  # 절대 import (frozen-safe)

if __name__ == "__main__":
    sys.exit(main())
