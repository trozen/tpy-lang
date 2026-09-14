# The yield is the fifth sink of the str/bytes view->owned chokepoint (the
# others being a return, a var-init, container literals/comprehensions and
# container inserts). A value that is STILL a view when it reaches the owning
# iterator slot must be copied there: string_view -> string is explicit, and
# span -> vector has no conversion at all.
#
# Most view locals never reach that sink any more -- a frame-unsafe source is
# promoted to owned during view deduction -- so what this case pins is the
# sources that are still views there: a literal source (static storage, so
# deduction leaves it a view), an annotated StrView (promotion must not touch a
# user's declared view contract), and the same two on the bytes side and at a
# single yield inside a trailing loop.
#
# The inverse is `owned_param_stays_bare`: a str param is copied into owned
# frame storage on the way in, so the sink must leave it alone rather than copy
# an owned string twice.
from typing import Iterator

from tpy import int32, StrView


class Holder:
    label: StrView

    def __init__(self, label: StrView) -> None:
        self.label = label

    def __enter__(self) -> StrView:
        return self.label

    def __exit__(self, et, ev, tb) -> None:
        pass


def static_source_view() -> Iterator[str]:
    lit = "static"  # tpyc: type(StrView)
    yield lit  # tpyc: ok
    yield "end"


def static_bytes_view() -> Iterator[bytes]:
    raw = b"xy"  # tpyc: type(BytesView)
    yield raw  # tpyc: ok
    yield raw


def explicit_view_local(s: StrView) -> Iterator[str]:
    v: StrView = s  # tpyc: ok
    yield v  # tpyc: ok
    yield "tail"


# NOT the intended output: the snapshot here is doubly conservative, and pins
# that rather than endorses it. `h` is held by reference and `__enter__` returns
# a view of `h.label`, so a view field would be sound -- but the with-target
# rule promotes on the enter type's CATEGORY, so `label` is an owning
# `std::string`, and the yield sink then copies an already-owned string. Both
# halves are filed in BUGS.md; a fix should show up as a delta here.
def with_view_target(h: Holder) -> Iterator[str]:
    with h as label:
        yield label  # tpyc: ok
        yield label


# A single yield inside the trailing loop: the same frame render as the
# two-yield generators above, kept so the copy at this loop shape stays pinned
# at exec level.
def peephole_view(n: int32) -> Iterator[str]:
    lit = "peephole"
    i = 0
    while i < n:
        yield lit  # tpyc: ok
        i += 1


def owned_param_stays_bare(s: str) -> Iterator[str]:
    yield s  # tpyc: ok
    yield s


def main() -> None:
    for v in static_source_view():
        print(v)

    for c in static_bytes_view():
        print(len(c))

    for v in explicit_view_local("borrowed"):
        print(v)

    for v in with_view_target(Holder("managed")):
        print(v)

    for v in peephole_view(2):
        print(v)

    for v in owned_param_stays_bare("kept"):
        print(v)


main()
