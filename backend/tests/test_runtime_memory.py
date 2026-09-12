import waterfallhunter.core.runtime_memory as runtime_memory


def test_trim_process_heap_runs_gc_and_malloc_trim(monkeypatch) -> None:
    calls = []

    monkeypatch.setattr(runtime_memory.gc, "collect", lambda: calls.append("gc") or 17)
    monkeypatch.setattr(runtime_memory, "_resolve_malloc_trim", lambda: lambda pad: calls.append(("trim", pad)) or 1)

    packet = runtime_memory.trim_process_heap()

    assert packet["gc_collected"] == 17
    assert packet["malloc_trim_available"] is True
    assert packet["malloc_trim_released"] is True
    assert calls == ["gc", ("trim", 0)]


def test_trim_process_heap_is_fail_safe_when_libc_trim_is_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(runtime_memory.gc, "collect", lambda: 0)
    monkeypatch.setattr(runtime_memory, "_resolve_malloc_trim", lambda: None)

    packet = runtime_memory.trim_process_heap()

    assert packet == {
        "gc_collected": 0,
        "malloc_trim_available": False,
        "malloc_trim_released": False,
    }
