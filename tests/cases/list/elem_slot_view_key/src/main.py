# A str/bytes lookup key at a list's LOOKUP slot (`remove`/`index`/`count`) is
# a read-only argument, so it passes as a VIEW like any other str/bytes
# argument and `container_ops.hpp`'s scan compares it through `tpy::key_eq`,
# which answers a view against an owned element directly -- no element is
# built.
# What the snapshot pins is the RENDER: no `std::string(...)` /
# `::tpy::Bytes(...)` at any of these calls. A snapshot cannot pin the
# non-allocation itself (that is which runtime overload wins, not what the
# call looks like); `lookup_key.hpp`'s static_asserts hold that half.
# The remove legs mutate the caller's list so the aliasing is observable, not
# just the return value; the index/count/len legs read.
from tpy import StrView


def remove_literal(xs: list[str]) -> None:
    xs.remove("b")  # tpyc: ok -- the literal stays a `const char[2]`


def remove_param(xs: list[str], k: str) -> None:
    xs.remove(k)  # tpyc: ok -- `k` is a std::string_view param


def remove_view_local(xs: list[str], src: str) -> None:
    v = src[1:]  # a StrView local, not an owned str
    xs.remove(v)  # tpyc: ok


def remove_slice(xs: list[str], src: str) -> None:
    # The same view source unbound: the render is shape-blind.
    xs.remove(src[1:])  # tpyc: ok


def remove_stripped(xs: list[str], src: str) -> None:
    # ... and a view-returning method call.
    xs.remove(src.strip())  # tpyc: ok


def remove_view_elem(vs: list[StrView], k: StrView) -> None:
    # A VIEW-typed element: the lookup key and the element are the same C++
    # type here.
    # Never called: building a `list[StrView]` local is not lowered yet
    # (a literal rejects at `expr.container_literal`, an append at
    # `method.arg_shape`), so this leg is pinned by its emitted C++ alone.
    vs.remove(k)  # tpyc: ok


def remove_owned_local(xs: list[str]) -> None:
    # An owned local is the element's own form and passes bare -- the same
    # runtime overload takes it, so both spellings reach one comparison.
    owned = "a" + "b"
    xs.remove(owned)  # tpyc: ok


def remove_element_read(xs: list[str]) -> None:
    # ... and so does a subscript read of the container itself.
    xs.remove(xs[0])  # tpyc: ok


def find(xs: list[str], k: str) -> int:
    return xs.index(k)  # tpyc: ok


def tally(xs: list[str], k: str) -> int:
    return xs.count(k)  # tpyc: ok


def drop_bytes_literal(bs: list[bytes]) -> None:
    # A bytes literal is a static span; the runtime compares it byte-wise
    # against the owned `std::vector<uint8_t>` elements.
    bs.remove(b"b")  # tpyc: ok


def drop_bytes_param(bs: list[bytes], k: bytes) -> None:
    bs.remove(k)  # tpyc: ok


def empty_keys(xs: list[str], bs: list[bytes], k: str, bk: bytes) -> None:
    # An EMPTY key: the comparison is a size check before any data read,
    # which matters for bytes -- an empty span may carry a null pointer.
    print(xs.count(k), xs.index(k), bs.count(bk), bs.index(bk))
    xs.remove(k)
    bs.remove(bk)
    print(xs, len(bs))


def key_len(k: str) -> int:
    # A plain free-function `str` param keeps the view form -- the same form
    # the lookup slots above receive.
    return len(k)


def main() -> None:
    xs = ["a", "b", "c", "b"]
    remove_literal(xs)
    print(xs)
    remove_param(xs, "c")
    print(xs)

    ys = ["a", "b", "c"]
    remove_view_local(ys, "?b")
    print(ys)
    remove_slice(ys, "?c")
    print(ys)
    remove_stripped(ys, " a ")
    print(ys)

    zs = ["ab", "c"]
    remove_owned_local(zs)
    print(zs)
    remove_element_read(zs)
    print(zs)

    ws = ["a", "b", "a"]
    print(find(ws, "b"), tally(ws, "a"))

    bs = [b"a", b"b", b"c"]
    drop_bytes_literal(bs)
    print(len(bs))
    drop_bytes_param(bs, b"a")
    print(len(bs), bs[0])

    empty_keys(["", "a"], [b"", b"a"], "", b"")

    print(key_len("abcd"))


main()
