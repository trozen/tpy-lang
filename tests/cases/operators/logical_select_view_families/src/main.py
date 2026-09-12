# Value-position `and`/`or` over the view families: bytes (a value view),
# Span[T] (a value view whose truthiness is the __len__ test) and bytearray
# (a REFERENCE type, so the select aliases -- it binds `std::vector<uint8_t>&`).
# The aliasing is pinned by the render unit rather than by a mutation here:
# mutating either operand of a container select anywhere in the body retypes
# the whole select to `bool` in sema
# (BUGS.md#container-select-mutation-retypes-bool), so the mutating spelling
# does not compile.
from tpy import int32, Span


def pick(a: bytes, b: bytes) -> int32:
    # Both operands are PARAMS, so both spell `std::span<const uint8_t>` at
    # runtime although their resolved type is the owned `bytes`; the select
    # compares the runtime spellings, not the resolved ones.
    return len(a or b)


def pick_local(a: bytes, b: bytes) -> int32:
    # The same span-spelled select at a LOCAL sink: the local keeps the view,
    # so no owning conversion is taken here.
    v = a or b  # tpyc: ok
    return len(v)


def pick_rebound(a: bytes, b: bytes) -> int32:
    # A REBOUND `bytes` param is still a view for the select's purposes, so
    # the pair stays same-spelling and the span composes over the live owned
    # local (BUGS.md#bytes-select-mixed-runtime-spelling).
    b = bytes([113, 114])
    v = a or b  # tpyc: ok
    return len(v)


class Store:
    data: bytes

    def __init__(self, data: bytes) -> None:
        self.data = data

    def put(self, a: bytes, b: bytes) -> None:
        # An OWNED `bytes` field sink over the same view-spelled select: the
        # store copies, so the field keeps the bytes after the caller's
        # argument buffers are gone.
        self.data = a or b  # tpyc: ok


def pick_owned(a: bytes, b: bytes) -> bytes:
    # The owned-RETURN sibling of the same select.
    return a or b  # tpyc: ok


def main() -> None:
    empty = b""
    data = b"xy"
    # bytes: the empty operand is falsy, so `or` yields the other one.
    print(len(empty or data))  # tpyc: ok
    print(len(data and empty))  # tpyc: ok
    print(len(data or empty))  # tpyc: ok
    print(pick(b"", b"xyz"))  # tpyc: ok
    print(pick_local(b"", b"abcd"))  # tpyc: ok
    print(pick_rebound(b"", b"z"))  # tpyc: ok
    # The owned sinks print the CHOSEN operand's bytes; a select that kept
    # the span here would not build (a span into an owned slot), so the
    # value check guards the operand choice, the C++ type guards the copy.
    st = Store(b"z")
    st.put(b"", b"pq")
    print(st.data)
    print(pick_owned(b"", b"ab"))

    xs = [1, 2, 3]
    ys: list[int32] = []
    s = Span[int32](xs)
    t = Span[int32](ys)
    # Span: the same select, tested with __len__ rather than .empty().
    u = t or s  # tpyc: ok
    print(len(u))

    ba = bytearray(b"z")
    bb = bytearray()
    # bytearray: the reference tier, `.empty()` truthiness like bytes.
    picked = bb or ba  # tpyc: ok
    print(len(picked))


main()
