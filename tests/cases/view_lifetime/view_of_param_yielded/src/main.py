# A view (StrView) derived from a parameter has safe provenance: yielding it
# from a generator (or returning it from a lambda) is sound -- the param data
# outlives the call -- and must not be rejected as a dangling local view.
#
# Two more sections cover a str/bytes local whose storage the deduction settles
# on OWNED: the generator frame field owns that buffer for the frame's whole
# lifetime, so a view of it is a valid yield root (`owned_local`), while a
# slice of a reference-typed param is copied INTO the frame rather than left
# pointing at the caller's object, which the caller grows across the suspension
# (`slice_of_ref_param`).
from tpy import StrView, int32
from typing import Iterator


def tails(s: str) -> Iterator[StrView]:
    yield s[1:]
    yield s[2:]


# generator, owned local: `strip()` returns a VIEW; a resumable body owns any
# non-static view source, so `v` is a frame field the yielded view can root in.
def owned_local(s: str) -> Iterator[StrView]:
    v = s.strip()
    yield v                  # tpyc: ok


# generator, reference-typed source: `ba` is the CALLER's bytearray (the frame
# holds a reference), so the slice must be an owned copy in the frame -- the
# caller grows `buf` across the yield below and `c` must still read b'0123'.
def slice_of_ref_param(ba: bytearray) -> Iterator[int32]:
    c = ba[0:4]              # tpyc: ok
    yield 0
    print("slice_of_ref_param:", bytes(c))
    yield 1


def main() -> None:
    for t in tails("hello"):
        print(t)

    for x in owned_local("  padded value long enough to reallocate  "):
        print("owned_local:", x)

    buf = bytearray(b"0123456789")
    for step in slice_of_ref_param(buf):
        if step == 0:
            for _ in range(2000):
                buf.append(65)  # tpyc: warning(/Mutation of 'buf' while iterating/)


main()
