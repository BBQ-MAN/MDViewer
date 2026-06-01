"""pytest 설정 — src 레이아웃을 import path 에 추가한다.

`pip install -e .` 없이도 `python -m pytest` 로 코어 테스트가 돌아가도록
src 디렉터리를 sys.path 에 주입한다.
"""

from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
