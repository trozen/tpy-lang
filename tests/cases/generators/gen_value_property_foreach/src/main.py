# The VALUE-returning half of the same family: a getter returning `StrView` or
# `str`, iterated inside a resumable frame, materialized into `__for_src_N`.
# The view flavour is the interesting one: `string_view::begin()` points into
# the field's own buffer, so its iterators are safe for the reason the return
# CONVENTION cannot express. The frame is still blind to a CALLER's mutation
# between two resumes (BUGS.md#frame-iter-loan-blind-to-caller).
# Own-returning sibling: generators/gen_own_property_foreach.
from typing import Iterator

from tpy import int32, StrView


class Holder:
    label: str

    def __init__(self) -> None:
        self.label = "ab"

    @property
    def view(self) -> StrView:
        return self.label

    @property
    def name(self) -> str:
        # the warning is the SUBJECT of this leg: an owned-str getter copies
        # the field, which is what makes the frame own what it iterates
        return self.label  # tpyc: warning(/returns a copy of str field/)


# view getter: the iterators point into the field's buffer, and the frame holds
# them across the yield
def gen_view(h: Holder) -> Iterator[int32]:
    n = 0
    for c in h.view:  # tpyc: ok
        print("view_char:", c)
        n += 1
        yield n


# owned-str getter: the frame owns the copy the getter handed back
def gen_name(h: Holder) -> Iterator[int32]:
    n = 0
    for c in h.name:  # tpyc: ok
        print("name_char:", c)
        n += 1
        yield n


def main() -> None:
    for v in gen_view(Holder()):
        print("view:", v)
    for v in gen_name(Holder()):
        print("name:", v)


main()
