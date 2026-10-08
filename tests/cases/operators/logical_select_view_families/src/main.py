# Value-position `and`/`or` over the view families: bytes (a value view),
# Span[T] (a value view whose truthiness is the __len__ test) and bytearray
# (a REFERENCE type, so the select aliases -- it binds `std::vector<uint8_t>&`),
# and the str twin: a view select stored into an owning union / Optional
# field.
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

    def put_either(self, a: bytes, b: bytes, first: bool) -> None:
        # The ternary twin: a select of two views stays a view, so the owned
        # field store copies it too.
        self.data = a if first else b  # tpyc: ok


class StrStore:
    u: str | int32
    o: str | None

    def __init__(self, a: str, first: bool) -> None:
        # ctor: a view param beside a literal is a view, so the owning union
        # and Optional members copy the whole select.
        self.u = a if first else "lit"  # tpyc: ok
        self.o = a or "lit"  # tpyc: ok

    def put(self, a: str, first: bool) -> None:
        # method: the ternary twin at body writes.
        self.u = a if first else "lit"  # tpyc: ok
        self.o = a if first else "lit"  # tpyc: ok

    def put_or(self, a: str) -> None:
        # method: the `or` twin at body writes.
        self.u = a or "lit"  # tpyc: ok
        self.o = a or "lit"  # tpyc: ok


def put_free(h: StrStore, a: str, first: bool) -> None:
    # free function: the same view select written through a holder.
    h.u = a if first else "lit"  # tpyc: ok
    h.o = a or "lit"  # tpyc: ok


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
    st.put_either(b"mn", b"op", False)
    print(st.data)
    print(pick_owned(b"", b"ab"))
    # str: the owning union / Optional fields keep the chosen text after
    # the argument buffer is gone.
    ss = StrStore("x" + "y", False)
    print("ctor.str_select", ss.u, ss.o)
    ss.put("p" + "q", True)
    print("method.str_ternary", ss.u, ss.o)
    ss.put_or("")
    print("method.str_or", ss.u, ss.o)
    ss.put_or("r" + "s")
    print("method.str_or_lhs", ss.u, ss.o)
    put_free(ss, "u" + "v", False)
    print("free.str_select", ss.u, ss.o)

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
