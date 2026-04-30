# BytesView in std::unordered_map / std::unordered_set: needs std::hash
# and std::equal_to specializations because std::span has no built-in
# operator==. User-level a==b uses ::tpy::bytes_eq directly.
#
# dict[BytesView, V] / set[BytesView] with bytes-literal keys is safe:
# codegen pins each literal to static storage via bytes_literal() because
# the dict's K type (BytesView) is threaded through the subscript /
# membership / set-add path. Without that, the temporary std::vector<uint8_t>
# would die at the call boundary and the stored span would dangle.
from tpy import BytesView

def test_set_with_literals() -> None:
    s: set[BytesView] = set()
    s.add(b"hello")
    s.add(b"world")
    s.add(b"hello")
    print(len(s))
    print(b"hello" in s)
    print(b"missing" in s)

def test_dict_with_literals() -> None:
    d: dict[BytesView, int] = {}
    d[b"alice"] = 1
    d[b"bob"] = 2
    d[b"alice"] = 10
    print(d[b"alice"])
    print(d[b"bob"])
    print(len(d))
    print(b"alice" in d)
    print(b"missing" in d)

def test_dict_with_bytes_keys() -> None:
    # The idiomatic form -- bytes elevates to owned vector at storage.
    d: dict[bytes, int] = {}
    d[b"a"] = 1
    d[b"b"] = 2
    d[b"a"] = 10
    print(d[b"a"])
    print(d[b"b"])
    print(len(d))

test_set_with_literals()
test_dict_with_literals()
test_dict_with_bytes_keys()
