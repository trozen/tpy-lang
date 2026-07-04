# An ANNOTATED owned-family local (x: str / x: bytes) fed a view-safe source
# and never mutated resolves as a VIEW: the annotation's view->owned coerce is
# stale and must render bare (was: string_view/span bound to a materialized
# owned temporary dying at end of statement -- silent UAF). Covers the plain,
# branch-hoisted, and generator-frame binding shapes. Mutated locals and
# owned returns still materialize (the inverse). The generated-code snapshot
# is the regression guard: the dangle often prints correctly off dead memory.
from typing import Iterator

from tpy import BytesView, StrView


def view_from_param(sv: StrView) -> None:
    label: str = sv  # tpyc: ok
    print(label, len(label))


def view_from_borrowing_call(s: str) -> None:
    t: str = s.strip()  # tpyc: ok
    print(t, len(t))


def bytes_view_from_param(bv: BytesView) -> None:
    label: bytes = bv  # tpyc: ok
    print(label, len(label))


def view_reassigned_from_view(sv: StrView, sv2: StrView) -> None:
    label: str = sv
    label = sv2  # tpyc: ok
    print(label)


def mutated_stays_owned(sv: StrView) -> str:
    m: str = sv
    m += "!"
    return m


def owned_return_still_copies(sv: StrView) -> str:
    return sv  # tpyc: ok


def hoisted_branch(sv: StrView, flag: bool) -> None:
    if flag:
        label: str = sv  # tpyc: ok
    else:
        label = "other"
    print(label)


def gen_frame(sv: StrView) -> Iterator[int]:
    label: str = sv  # tpyc: ok
    print(label)
    yield len(label)


def main() -> None:
    view_from_param("hello")
    view_from_borrowing_call("  hi  ")
    bytes_view_from_param(b"abc")
    view_reassigned_from_view("one", "two")
    print(mutated_stays_owned("own"))
    print(owned_return_still_copies("ret"))
    hoisted_branch("first", True)
    hoisted_branch("first", False)
    for n in gen_frame("frame"):
        print(n)


main()
