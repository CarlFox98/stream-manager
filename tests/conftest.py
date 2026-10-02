"""Shared isolation: no test may write into the real data/ folder."""
import pytest


@pytest.fixture(autouse=True)
def _isolate_wheel_state(tmp_path, monkeypatch):
    import time
    from stream_manager import chatfeed, featured, owed, stats, timed
    # never let a test kick off a real 7TV/BTTV/FFZ fetch
    monkeypatch.setattr(chatfeed, "_tp_cache", {"map": {}, "at": time.time() + 1e9, "ok": True})
    monkeypatch.setattr(stats, "STATS_FILE", str(tmp_path / "stats.json"))
    monkeypatch.setattr(timed, "_FILE", str(tmp_path / "active-effects.json"))
    monkeypatch.setattr(owed, "_FILE", str(tmp_path / "owed.json"))
    monkeypatch.setattr(featured, "_FILE", str(tmp_path / "featured.json"))
    timed._active.clear()
    owed._items = []
    owed._loaded = False
    yield
    timed._active.clear()
