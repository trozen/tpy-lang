# Bytes subscript on list[bytes] infers BytesView; falls back to owned bytes
# if the list is mutated after the access (mirrors str_list_view for bytes).
from tpy import Int32

def test_list_view() -> None:
    """No mutation: BytesView."""
    items: list[bytes] = [b"alice", b"bob"]
    x = items[Int32(0)]  # tpyc: type(BytesView)
    print(x)

def test_list_mutation_fallback() -> None:
    """Source mutated after borrow: falls back to owned bytes."""
    items: list[bytes] = [b"alice", b"bob"]
    x = items[Int32(0)]  # tpyc: type(bytes)
    items.append(b"carol")
    print(x)

def test_list_reassign_fallback() -> None:
    """Source reassigned after borrow: falls back to owned bytes."""
    items: list[bytes] = [b"alice", b"bob"]
    x = items[Int32(0)]  # tpyc: type(bytes)
    items = [b"dave"]
    print(x)

def test_list_subscript_write_fallback() -> None:
    """Subscript write on source: falls back to owned bytes."""
    items: list[bytes] = [b"alice", b"bob"]
    x = items[Int32(0)]  # tpyc: type(bytes)
    items[Int32(0)] = b"eve"
    print(x)

test_list_view()
test_list_mutation_fallback()
test_list_reassign_fallback()
test_list_subscript_write_fallback()
