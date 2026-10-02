# A list[bytes] element read binds owned bytes (a container element never lends a
# view); each write after the read must keep CPython's value (mirrors str_list_view).
from tpy import int32

def test_list_view() -> None:
    """No mutation: still an owned copy."""
    items: list[bytes] = [b"alice", b"bob"]
    x = items[int32(0)]  # tpyc: type(bytes)
    print(x)

def test_list_mutation_fallback() -> None:
    """Source grows after the read: the copy keeps the old value."""
    items: list[bytes] = [b"alice", b"bob"]
    x = items[int32(0)]  # tpyc: type(bytes)
    items.append(b"carol")
    print(x)

def test_list_reassign_fallback() -> None:
    """Source reassigned after the read: the copy keeps the old value."""
    items: list[bytes] = [b"alice", b"bob"]
    x = items[int32(0)]  # tpyc: type(bytes)
    items = [b"dave"]
    print(x)

def test_list_subscript_write_fallback() -> None:
    """Subscript write on the source: the copy keeps the old value."""
    items: list[bytes] = [b"alice", b"bob"]
    x = items[int32(0)]  # tpyc: type(bytes)
    items[int32(0)] = b"eve"
    print(x)

test_list_view()
test_list_mutation_fallback()
test_list_reassign_fallback()
test_list_subscript_write_fallback()
