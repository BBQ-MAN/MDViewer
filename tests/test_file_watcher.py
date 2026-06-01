# -*- coding: utf-8 -*-
"""file_watcher.py 코어 단위 테스트 (PySide6 무의존).

계약(§4): FileWatcher(on_changed) / watch(path) / stop() 멱등 / 디바운스.
실제 파일을 수정해 watchdog 이벤트 → 디바운스 → 콜백 1회를 검증한다.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

from mdviewer.file_watcher import FileWatcher


def _wait_until(predicate, timeout=5.0, interval=0.02) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if predicate():
            return True
        time.sleep(interval)
    return False


def test_construct_and_stop_idempotent():
    w = FileWatcher(on_changed=lambda: None)
    # watch 시작 전 stop 도 안전(멱등).
    w.stop()
    w.stop()
    assert w.path is None


def test_watch_sets_path(tmp_path):
    p = tmp_path / "f.md"
    p.write_text("init", encoding="utf-8")
    w = FileWatcher(on_changed=lambda: None)
    try:
        w.watch(p)
        assert w.path == p
    finally:
        w.stop()


def test_callback_fires_on_modify(tmp_path):
    p = tmp_path / "f.md"
    p.write_text("init", encoding="utf-8")

    event = threading.Event()
    count = {"n": 0}

    def on_changed():
        count["n"] += 1
        event.set()

    w = FileWatcher(on_changed=on_changed)
    try:
        w.watch(p)
        time.sleep(0.3)  # observer 가 뜰 시간
        p.write_text("changed!", encoding="utf-8")
        assert event.wait(timeout=5.0), "수정 후 on_changed 콜백이 호출돼야 한다"
    finally:
        w.stop()


def test_debounce_collapses_burst(tmp_path):
    p = tmp_path / "f.md"
    p.write_text("init", encoding="utf-8")

    count = {"n": 0}
    lock = threading.Lock()

    def on_changed():
        with lock:
            count["n"] += 1

    w = FileWatcher(on_changed=on_changed)
    try:
        w.watch(p)
        time.sleep(0.3)
        # 디바운스 윈도우(150ms) 안에 연속 5회 쓰기 → 콜백 1회로 합쳐져야 함.
        for i in range(5):
            p.write_text(f"v{i}", encoding="utf-8")
            time.sleep(0.01)
        # 디바운스 + 처리 시간 대기.
        _wait_until(lambda: count["n"] >= 1, timeout=5.0)
        time.sleep(0.4)
        assert count["n"] >= 1, "버스트 후 최소 1회 콜백"
        assert count["n"] <= 2, f"디바운스로 콜백이 합쳐져야 한다(실제 {count['n']}회)"
    finally:
        w.stop()


def test_watch_replaces_target(tmp_path):
    a = tmp_path / "a.md"
    b = tmp_path / "b.md"
    a.write_text("a", encoding="utf-8")
    b.write_text("b", encoding="utf-8")

    fired = {"path": None}
    event = threading.Event()

    def on_changed():
        event.set()

    w = FileWatcher(on_changed=on_changed)
    try:
        w.watch(a)
        time.sleep(0.2)
        w.watch(b)  # 교체
        assert w.path == b
        time.sleep(0.3)
        # 이제 b 변경이 감지돼야 한다.
        event.clear()
        b.write_text("b2", encoding="utf-8")
        assert event.wait(timeout=5.0), "교체된 대상(b)의 변경이 감지돼야 한다"
    finally:
        w.stop()


def test_callback_exception_isolated(tmp_path):
    # 콜백이 예외를 던져도 watcher 가 죽지 않아야 함(워커 스레드 격리, core notes §7).
    p = tmp_path / "f.md"
    p.write_text("init", encoding="utf-8")

    hits = {"n": 0}

    def boom():
        hits["n"] += 1
        raise RuntimeError("intentional")

    w = FileWatcher(on_changed=boom)
    try:
        w.watch(p)
        time.sleep(0.3)
        p.write_text("changed", encoding="utf-8")
        # 예외가 격리되어 프로세스가 죽지 않고, 호출은 일어남.
        assert _wait_until(lambda: hits["n"] >= 1, timeout=5.0)
    finally:
        w.stop()  # 예외 후에도 stop 정상.
    assert w.path is None
