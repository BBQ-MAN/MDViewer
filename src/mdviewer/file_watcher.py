"""파일 변경 감시 (비-GUI 코어).

이 모듈은 **PySide6에 의존하지 않는다.** watchdog 만 사용한다.

청사진(_workspace/01_architect_blueprint.md) 4절의 FileWatcher 계약을 구현한다:
    - FileWatcher(on_changed)
    - watch(path)   : 파일 교체 시 기존 감시 대체
    - stop()        : 멱등

⚠️ on_changed 콜백은 watchdog 워커 스레드에서 호출된다. UI 는 이 콜백 안에서
   위젯을 직접 건드리지 말고 Qt Signal.emit() 만 해야 한다(4.1 참조).
콜백 폭주(에디터 atomic-save 다중 이벤트)를 막기 위해 ~150ms 디바운스를 내장한다.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

__all__ = ["FileWatcher"]

# 디바운스 윈도우(초). 저장 중 다중 이벤트를 단일 콜백으로 합친다.
_DEBOUNCE_SECONDS = 0.15


class _Handler(FileSystemEventHandler):
    """대상 파일명에 해당하는 이벤트만 골라 디바운스 후 콜백을 트리거한다.

    에디터는 atomic-save(임시 파일 → rename)로 저장하므로 created/moved/modified
    를 모두 본다. 대상 파일을 가리키는 모든 이벤트를 변경으로 취급한다.
    """

    def __init__(self, target: Path, fire: Callable[[], None]) -> None:
        super().__init__()
        # 동일 파일 비교를 위해 정규화된 절대 경로를 사용.
        self._target = target
        self._target_name = target.name
        try:
            self._target_resolved = target.resolve()
        except OSError:
            self._target_resolved = target
        self._fire = fire

    def _matches(self, event_path: str | bytes) -> bool:
        if isinstance(event_path, bytes):
            try:
                event_path = event_path.decode("utf-8", "replace")
            except Exception:
                return False
        p = Path(event_path)
        if p.name == self._target_name:
            return True
        try:
            return p.resolve() == self._target_resolved
        except OSError:
            return False

    def on_any_event(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        # modified/created/moved(dest) 등 대상 파일을 가리키면 변경으로 본다.
        if self._matches(event.src_path):
            self._fire()
            return
        dest = getattr(event, "dest_path", None)
        if dest and self._matches(dest):
            self._fire()


class FileWatcher:
    """단일 파일의 변경을 감시한다. watchdog 워커 스레드에서 콜백을 호출한다.

    PySide6 무의존. UI 가 콜백 안에서 Qt Signal 을 emit 해 메인 스레드로 넘긴다.
    """

    def __init__(self, on_changed: Callable[[], None]) -> None:
        """on_changed: 감시 대상 파일이 수정되면 호출되는 콜백(인자 없음).

        ⚠️ watchdog 워커 스레드에서 호출되므로, UI 는 이 콜백에서 직접 위젯을
           건드리지 말고 Qt Signal.emit() 만 해야 한다(스레드 안전).
        """
        self._on_changed = on_changed
        self._lock = threading.RLock()
        self._observer: Observer | None = None  # type: ignore[valid-type]
        self._watch = None  # ObservedWatch
        self._path: Path | None = None
        self._debounce_timer: threading.Timer | None = None

    # -- 디바운스 ----------------------------------------------------------
    def _fire_debounced(self) -> None:
        """워커 스레드에서 호출됨. 디바운스 윈도우 내 중복을 단일 콜백으로 합친다."""
        with self._lock:
            if self._debounce_timer is not None:
                self._debounce_timer.cancel()
            self._debounce_timer = threading.Timer(
                _DEBOUNCE_SECONDS, self._emit
            )
            self._debounce_timer.daemon = True
            self._debounce_timer.start()

    def _emit(self) -> None:
        with self._lock:
            self._debounce_timer = None
        try:
            self._on_changed()
        except Exception:
            # 콜백 예외가 감시 스레드를 죽이지 않도록 격리.
            pass

    # -- 공개 API ----------------------------------------------------------
    def watch(self, path: Path) -> None:
        """대상 파일을 감시 시작한다. 다른 파일이 이미 감시 중이면 교체한다.

        부모 디렉터리를 watchdog Observer 로 감시하고 해당 파일명 이벤트만
        필터링한다(에디터 atomic-save 대응).
        """
        path = Path(path)
        with self._lock:
            # 기존 감시 대체: Observer 는 재사용하되 스케줄만 교체.
            if self._observer is not None and self._watch is not None:
                try:
                    self._observer.unschedule(self._watch)
                except Exception:
                    pass
                self._watch = None

            self._path = path
            parent = path.parent if str(path.parent) else Path(".")

            handler = _Handler(path, self._fire_debounced)

            if self._observer is None:
                self._observer = Observer()

            try:
                self._watch = self._observer.schedule(
                    handler, str(parent), recursive=False
                )
            except (OSError, FileNotFoundError):
                # 부모 디렉터리가 사라졌거나 접근 불가 — 조용히 무시(크래시 금지).
                self._watch = None
                return

            if not self._observer.is_alive():
                self._observer.start()

    def stop(self) -> None:
        """감시를 중지하고 watchdog Observer 스레드를 정리한다. 멱등."""
        with self._lock:
            if self._debounce_timer is not None:
                self._debounce_timer.cancel()
                self._debounce_timer = None
            observer = self._observer
            self._observer = None
            self._watch = None
            self._path = None

        if observer is not None:
            try:
                observer.unschedule_all()
            except Exception:
                pass
            try:
                observer.stop()
            except Exception:
                pass
            try:
                observer.join(timeout=2.0)
            except Exception:
                pass

    @property
    def path(self) -> Path | None:
        """현재 감시 중인 파일 경로(없으면 None)."""
        return self._path
