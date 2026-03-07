# Warn on dict mutation during iteration (borrow conflict)
from tpy import Int32

def test_pop() -> None:
    d: dict[str, Int32] = {"a": Int32(1)}
    for k in d:
        d.pop(k)  # tpyc: warning(/Mutation of 'd'.*'pop'/)

def test_clear() -> None:
    d: dict[str, Int32] = {"a": Int32(1)}
    for k in d:
        d.clear()  # tpyc: warning(/Mutation of 'd'.*'clear'/)

def test_update() -> None:
    d: dict[str, Int32] = {"a": Int32(1)}
    other: dict[str, Int32] = {"b": Int32(2)}
    for k in d:
        d.update(other)  # tpyc: warning(/Mutation of 'd'.*'update'/)

def test_setdefault() -> None:
    d: dict[str, Int32] = {"a": Int32(1)}
    for k in d:
        d.setdefault(k, Int32(0))  # tpyc: warning(/Mutation of 'd'.*'setdefault'/)

def test_del() -> None:
    d: dict[str, Int32] = {"a": Int32(1)}
    for k in d:
        del d[k]  # tpyc: warning(/Mutation of 'd'.*'del'/)

def test_conditional_mutation() -> None:
    d: dict[str, Int32] = {"a": Int32(1), "b": Int32(2)}
    for k in d:
        if k == "a":
            d.pop(k)  # tpyc: warning(/Mutation of 'd'/)

def test_no_warn_after_loop() -> None:
    """Mutation after loop exit is fine."""
    d: dict[str, Int32] = {"a": Int32(1)}
    for k in d:
        pass
    d["b"] = Int32(2)  # tpyc: ok

def test_read_only_ok() -> None:
    """No warnings for read-only operations during iteration."""
    d: dict[str, Int32] = {"a": Int32(1), "b": Int32(2)}
    total: Int32 = Int32(0)
    for k in d:
        total += d[k]    # tpyc: ok
        _ = len(d)        # tpyc: ok
