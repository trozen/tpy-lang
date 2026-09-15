# What stays legal once a borrow return off a REBOUND module global is
# rejected: an owned return of a rebound global (whatever spells the rebind --
# `=`, a walrus), a borrow return of a global nothing rebinds, a value element
# COPIED out of a rebound global into a tuple (the reference-typed twin of
# that read is `error_reference_global_rebind_value_tuple`), a `global`
# declaration whose body only reads the slot, and a global that already holds
# a HANDLE (the return copies the handle, so the rebind cannot free what it
# points at).
# The `+=` spelling of a rebind is pinned where it asserts something -- as the
# rebind of `error_view_returned_off_rebound_global`; an owned return compiles
# whether or not the walk counts it.
# Both global spellings appear (BANNER inferred, LABEL annotated).
from typing import Iterator

from tpy import Ptr, StrView, int32, take_ptr

BANNER = "banner text long enough that no small-string buffer hides a reuse"
MOTTO = "onward"
LABEL: str = "label text long enough that no small-string buffer hides a reuse"
TITLE = "title text long enough that no small-string buffer hides a reuse"
XS = [1, 2, 3]
COUNT = 0


class Slot:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


LEFT = Slot(10)
RIGHT = Slot(20)
CUR: Ptr[Slot] = None


class Registry:
    tag: int32

    def __init__(self) -> None:
        self.tag = 0

    # method: borrowing a global nothing rebinds stays a view return
    def banner(self) -> StrView:
        return BANNER  # tpyc: ok


# free function: the owned escape hatch for a global `relabel` rebinds
def label() -> str:
    return LABEL  # tpyc: ok


def relabel() -> None:
    global LABEL
    LABEL = "relabelled"


# free function: the `+=` spelling of a rebind, exercised at run time on a
# str global; the owned return stays legal (the borrow rule is pinned by
# `error_view_returned_off_rebound_global`)
def motto() -> str:
    return MOTTO  # tpyc: ok


def extend_motto() -> None:
    global MOTTO
    MOTTO += "!"


# A nested-def rebind is its own scope and is counted the same way, but a
# `global X` write inside a nested def does not lower yet
# (stmt.global:global.unseeded), so no section can hold it.


# free function: a VALUE element copied out of a rebound global is not a
# borrow of the slot, so the tuple arm leaves it alone -- the same verdict the
# scalar `return len(LABEL)` gets
def label_size() -> tuple[int32, int32]:
    return (len(LABEL), 1)  # tpyc: ok


# free function: and when the rebind is spelled as a walrus inside a condition
def title() -> str:
    return TITLE  # tpyc: ok


def retitle() -> None:
    global TITLE
    if (TITLE := "retitled"):
        pass


# free function: `global` with no write to TAGLINE only READS the slot, so a
# borrow return of it is still legal even though the declaration is there
TAGLINE = "tagline text long enough that no small-string buffer hides a reuse"


def read_tagline() -> StrView:
    global TAGLINE
    return TAGLINE  # tpyc: ok


def read_count() -> int32:
    global COUNT
    return COUNT  # tpyc: ok


def bump() -> None:
    global COUNT
    COUNT += 1


# free function: a reference-typed global nothing rebinds is returned as a
# borrow -- the caller mutates through it and XS observes the write
def items() -> list[int32]:
    return XS  # tpyc: ok


# free function: a global that HOLDS a handle is exempt -- `point_at` rebinds
# CUR, but the return copies the pointer out of the slot rather than pointing
# into it (the asyncio `_current_executor` shape)
def current() -> Ptr[Slot]:
    return CUR  # tpyc: ok


def point_at(s: Ptr[Slot]) -> None:
    global CUR
    CUR = s


# generator: the same borrow rule at a yield
def banners() -> Iterator[StrView]:
    yield BANNER  # tpyc: ok
    yield BANNER[0:6]  # tpyc: ok


def main() -> None:
    r = Registry()
    print("method", r.banner())
    print("owned", label())
    n, k = label_size()
    print("valueelem", n, k)
    relabel()
    print("owned", label())
    print("motto", motto())
    extend_motto()
    print("motto", motto())
    n, k = label_size()
    print("valueelem", n, k)
    retitle()
    print("walrus", title())
    print("readonly-global", read_tagline())
    bump()
    print("readonly", read_count())
    got = items()
    got.append(4)
    print("alias", len(XS), XS[3])
    point_at(take_ptr(LEFT))
    print("handle", current().v)
    point_at(take_ptr(RIGHT))
    print("handle", current().v)
    for b in banners():
        print("yield", b)


main()
