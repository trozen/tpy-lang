# Warn on set mutation during iteration (borrow conflict)
from tpy import int32

def test_add() -> None:
    s: set[int32] = {int32(1), int32(2)}
    for x in s:
        s.add(x)  # tpyc: warning(/Mutation of 's'.*'add'/)

def test_remove() -> None:
    s: set[int32] = {int32(1), int32(2)}
    for x in s:
        s.remove(x)  # tpyc: warning(/Mutation of 's'.*'remove'/)

def test_discard() -> None:
    s: set[int32] = {int32(1), int32(2)}
    for x in s:
        s.discard(x)  # tpyc: warning(/Mutation of 's'.*'discard'/)

def test_pop() -> None:
    s: set[int32] = {int32(1), int32(2)}
    for x in s:
        s.pop()  # tpyc: warning(/Mutation of 's'.*'pop'/)

def test_clear() -> None:
    s: set[int32] = {int32(1), int32(2)}
    for x in s:
        s.clear()  # tpyc: warning(/Mutation of 's'.*'clear'/)

def test_update() -> None:
    s: set[int32] = {int32(1), int32(2)}
    other: set[int32] = {int32(3)}
    for x in s:
        s.update(other)  # tpyc: warning(/Mutation of 's'.*'update'/)

def test_no_warn_after_loop() -> None:
    """Mutation after loop exit is fine."""
    s: set[int32] = {int32(1), int32(2)}
    for x in s:
        pass
    s.add(int32(3))  # tpyc: ok

def test_read_only_ok() -> None:
    """No warnings for read-only operations during iteration."""
    s: set[int32] = {int32(1), int32(2)}
    total: int32 = int32(0)
    for x in s:
        total += x     # tpyc: ok
        _ = len(s)     # tpyc: ok
