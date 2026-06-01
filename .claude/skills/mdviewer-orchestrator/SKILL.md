---
name: mdviewer-orchestrator
description: MDViewer(Python/PySide6 마크다운 뷰어 Windows 앱) 개발 전체를 조율하는 오케스트레이터. 아키텍처 설계→코어 엔진·UI 병렬 구현→QA 검증→exe 패키징의 에이전트 팀 워크플로우를 실행한다. MDViewer를 만들거나, 기능을 추가/수정/보완하거나, "다시 실행/재실행/업데이트/빌드/패키징"하거나, 마크다운 뷰어·렌더링·PySide6 UI·exe 빌드 관련 작업을 요청할 때 반드시 이 스킬을 사용. 단순 질문은 직접 응답 가능.
---

# MDViewer Orchestrator — 마크다운 뷰어 빌드 조율

Python/PySide6 마크다운 뷰어 **MDViewer**를 에이전트 팀으로 개발한다. 이 스킬은 "누가 언제 어떤 순서로 협업하는가"를 정의한다. 각 에이전트의 "어떻게"는 해당 스킬이 담는다.

**실행 모드:** 에이전트 팀 (생성-검증 파이프라인). 모든 Agent/팀원 호출은 `model: "opus"`.

## 팀 구성

| 에이전트 | 타입 | 스킬 | 담당 |
|---------|------|------|------|
| architect | architect | python-desktop-architecture | 청사진·계약·구조 |
| core-engine-dev | core-engine-dev | markdown-rendering | renderer.py, file_watcher.py |
| ui-dev | ui-dev | pyside6-app-patterns | main_window.py, app.py, theme.py |
| qa-verifier | general-purpose | desktop-app-qa | 경계면 교차 검증, 실행 검증 |
| packager | packager | pyinstaller-packaging | mdviewer.spec, .exe 빌드 |

## Phase 0: 컨텍스트 확인 (항상 먼저)

`_workspace/`와 `src/mdviewer/` 존재 여부로 실행 모드를 판별한다:

- **초기 실행**: `_workspace/` 없음 → 아래 Phase 1~5 전체 실행.
- **부분 재실행**: 산출물 존재 + 사용자가 특정 부분 수정 요청(예: "다크모드 색만 고쳐", "코드 하이라이트 테마 추가") → 해당 에이전트만 재호출하고 QA로 회귀 검증.
- **새 실행**: 산출물 존재 + 요구사항이 크게 바뀜 → 기존 `_workspace/`를 `_workspace_prev/`로 옮기고 초기 실행처럼 진행.

사용자 요청을 읽고 어느 모드인지 먼저 판단해 알린 뒤 진행한다.

## Phase 1: 설계 (architect 단독)

architect를 호출해 `_workspace/01_architect_blueprint.md`를 생성한다. 청사진에 렌더 API 계약·디렉토리 구조·의존성·패키징 base path 규칙이 모두 있는지 확인하고, 빠지면 보완을 지시한다. **계약이 확정되기 전엔 개발 단계로 넘어가지 않는다** — 계약이 병렬 개발의 토대다.

## Phase 2: 병렬 구현 (팀: core-engine-dev ∥ ui-dev + 점진 QA)

`TeamCreate`로 팀(core-engine-dev, ui-dev, qa-verifier)을 구성하고 `TaskCreate`로 작업을 분배한다.

**데이터 흐름:**
- 두 개발자는 `01_architect_blueprint.md`의 계약을 입력으로 받아 **병렬로** 시작한다.
- core-engine-dev는 공개 API 확정 시 `02_core_engine_notes.md`를 쓰고 ui-dev·qa에게 `SendMessage`로 통지.
- ui-dev는 계약 모호점이 있으면 추측하지 말고 core-engine-dev에게 `SendMessage`로 질의(경계면 추측이 통합 버그의 주원인).
- **점진적 QA**: core 엔진 완성 즉시 qa-verifier가 코어 단위 테스트 실행 → PASS 후 ui-dev 통합 진행. UI 완성 즉시 qa-verifier가 경계면 교차 비교.

**조율 원칙:**
- 계약 변경은 반드시 architect·양 개발자·qa 모두에게 전파(한쪽만 알면 버그).
- 작업 상태는 `TaskUpdate`로 공유, 산출물은 `src/`·`_workspace/`에 파일로, 실시간 소통은 `SendMessage`로.

## Phase 3: 통합 검증 (qa-verifier)

전체 통합 후 qa-verifier가 `_workspace/04_qa_report.md`를 작성한다. 항목: 경계면 교차 비교, 코어 단위 테스트, 앱 스모크 실행, 렌더 정확성·견고성. FAIL은 책임 에이전트에게 `SendMessage`로 전달해 1회 수정→재검증. 남은 FAIL은 미해결로 명시.

## Phase 4: 패키징 (packager) — QA PASS 게이트

`04_qa_report.md`가 PASS인 항목에 한해 packager를 호출한다(FAIL 잔존 시 패키징 보류, 사용자에 보고). packager는 `mdviewer.spec`을 만들고 빌드한 뒤 **실제 .exe를 실행 검증**한다. 결과는 `05_package_notes.md`에.

## Phase 5: 종합 및 피드백

산출물(소스, exe, 리포트)을 사용자에게 요약 보고하고 실행법을 안내한다. 그 후 피드백을 요청한다: "렌더 품질·UI·빌드에서 개선할 부분이 있나요? 팀 구성이나 워크플로우를 바꾸고 싶으세요?" 피드백은 Phase 7(하네스 진화) 경로로 반영한다.

## 데이터 전달 규칙

- 중간 산출물은 `_workspace/{phase}_{agent}_{artifact}.md` 규칙으로 저장하고 보존(감사 추적).
- 앱 소스는 `src/mdviewer/`, 배포물은 `dist/`.
- 권장 조합: 태스크 기반(조율) + 파일 기반(산출물·계약) + 메시지 기반(실시간 소통).

## 에러 핸들링

- 에이전트 실패 시 1회 재시도. 재실패하면 해당 산출물 없이 진행하되 최종 보고에 누락을 명시.
- 계약 충돌(core vs ui 이견)은 삭제·일방 결정하지 말고 architect 중재로 합의, 양쪽 통지.
- QA FAIL이 패키징 게이트를 막으면 빌드를 강행하지 않고 사용자에 보고.
- PySide6/WebEngine은 Windows 런타임에서만 드러나는 문제가 있으니, QA·packager의 "실제 실행 검증"을 건너뛰지 않는다.

## 테스트 시나리오

**정상 흐름:** "마크다운 뷰어 만들어줘" → Phase 0(초기 실행 판별) → architect 청사진 → core·ui 병렬 구현 + 점진 QA → 통합 QA PASS → packager가 exe 빌드·실행 검증 → 사용자에 `python -m mdviewer` 및 `dist/MDViewer.exe` 안내.

**에러 흐름:** QA가 경계면 불일치 발견(UI가 `result["html"]`로 접근하나 엔진은 dataclass `result.html` 반환) → qa-verifier가 ui-dev에 `SendMessage` 재현 절차 전달 → ui-dev 수정 → qa 재검증 PASS → 그 후에만 packager 진행. 만약 수정 후에도 FAIL이면 리포트에 미해결 명시, 패키징 보류, 사용자 보고.

**부분 재실행:** "다크 테마 코드 색이 안 맞아" → Phase 0에서 부분 재실행 판별 → ui-dev만 재호출(theme.py 수정) → qa-verifier가 테마 회귀 검증 → 보고. architect·core·packager는 건드리지 않음.
