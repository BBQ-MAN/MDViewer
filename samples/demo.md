# MDViewer 데모 문서

이 문서는 렌더링 엔진과 UI를 검증하기 위한 종합 샘플이다.
헤딩 계층, 코드블록(여러 언어), 테이블, 각주, 작업목록, 상대경로 이미지,
내부 앵커 링크를 모두 포함한다.

## 목차 검증용 헤딩 계층

목차(TOC)는 아래 헤딩들로부터 자동 생성되어야 한다.

### 1단계 하위 섹션
### 2단계 하위 섹션
#### 더 깊은 헤딩 (h4)

## 내부 앵커 링크

- [소개로 이동](#mdviewer-데모-문서)
- [코드 하이라이트 섹션으로 이동](#코드-하이라이트)
- [테이블 섹션으로 이동](#테이블)
- [각주 섹션으로 이동](#각주)

위 링크를 클릭하면 문서 내 해당 섹션으로 스크롤되어야 한다.

## 코드 하이라이트

### Python

```python
from pathlib import Path

def render_markdown(text: str, base_dir: Path) -> str:
    """마크다운을 HTML로 변환한다."""
    if not text.strip():
        return "<p><em>(빈 문서)</em></p>"
    return convert(text, base_dir=base_dir)
```

### JavaScript

```javascript
const greet = (name) => {
  console.log(`Hello, ${name}!`);
  return name.length;
};
greet("MDViewer");
```

### Bash

```bash
pip install -r requirements.txt
python -m mdviewer samples/demo.md
```

### 언어 미지정 / 인라인

인라인 코드: `print("inline")` 와 `git status`.

```
언어를 지정하지 않은 일반 코드 블록.
하이라이트 없이 monospace 로 렌더되어야 한다.
```

## 테이블

| 기능          | 담당 에이전트     | 상태   |
|---------------|-------------------|--------|
| 렌더링 엔진   | core-engine-dev   | 설계됨 |
| UI            | ui-dev            | 설계됨 |
| 패키징        | packager          | 대기   |
| 정렬 테스트   | :---: 중앙        | 우측 → |

## 작업목록 (Task list)

- [x] 아키텍처 청사진 작성
- [x] 렌더 API 계약 확정
- [ ] 코어 엔진 구현
- [ ] UI 구현
- [ ] exe 패키징

## 인용과 강조

> 이것은 인용 블록이다.
> 코어는 PySide6 없이 동작해야 한다는 원칙을 기억하라.

**굵게**, *기울임*, ~~취소선~~, 그리고 `코드`.

## 이미지 (상대경로 + 견고성)

아래 이미지는 의도적으로 자리표시 파일이 없을 수 있다 — 깨진 이미지도
렌더러가 예외 없이 처리하는지 검증한다.

![로고](img/logo.png "MDViewer 로고")

상대경로 `img/logo.png` 는 `render()` 의 `base_dir` 기준으로 해석되어야 한다.

## 각주

마크다운 뷰어는 각주를 지원한다[^1]. 두 번째 각주도 확인한다[^note].

[^1]: 첫 번째 각주의 내용이다.
[^note]: 이름 붙은 각주. 문서 하단에 모여 렌더된다.

## 중첩 리스트

1. 첫째 항목
   1. 하위 항목 A
   2. 하위 항목 B
2. 둘째 항목
   - 불릿 하위
   - 또 다른 불릿

## 수평선

위와 아래를 가르는 구분선:

---

문서 끝. [맨 위로](#mdviewer-데모-문서)
