# bytes is hashable and can be used as dict keys and set elements.
# BytesView supports hash() but cannot be used as a container key.
from tpy import Int32

def test_hash_basic() -> None:
    h1 = hash(b"hello")
    h2 = hash(b"hello")
    h3 = hash(b"world")
    print(h1 == h2)
    print(h1 != h3)

def test_dict_key() -> None:
    d: dict[bytes, str] = {b"alice": "A", b"bob": "B"}
    print(d[b"alice"])
    print(d[b"bob"])
    print(len(d))

def test_set_element() -> None:
    s: set[bytes] = {b"x", b"y", b"x"}
    print(len(s))

def test_bytes_view_hash() -> None:
    items: list[bytes] = [b"hello", b"world"]
    v = items[Int32(0)]  # tpyc: type(BytesView)
    h1 = hash(v)
    h2 = hash(b"hello")
    print(h1 == h2)

test_hash_basic()
test_dict_key()
test_set_element()
test_bytes_view_hash()
