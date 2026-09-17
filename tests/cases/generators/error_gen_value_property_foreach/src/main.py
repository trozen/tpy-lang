# A @property whose getter returns a VALUE type, iterated inside a resumable
# frame: the fence asks the return CONVENTION, so these join the `Own[...]`
# getter (BUGS.md#own-property-iterable-materialize).
# Own-returning sibling: error_gen_own_property_foreach.
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
        return self.label  # tpyc: warning(/returns a copy of str field/)


class Outer:
    inner: Holder

    def __init__(self) -> None:
        self.inner = Holder()


# view getter, one hop -- the annotated leg, chosen because it is the one the
# fence costs: these iterators point into the field's buffer and were safe
# (BUGS.md#frame-iter-loan-blind-to-caller)
def gen_view(h: Holder) -> Iterator[int32]:  # tpyc: error(/res.by_value_property_iter/)
    for c in h.view:
        print(c)
        yield 1


# str getter, one hop: same tag, unannotated since the compile stops at the
# first error
def gen_name(h: Holder) -> Iterator[int32]:
    for c in h.name:
        print(c)
        yield 1


# str getter, two hops: the depth does not change the reason
def gen_deep_name(o: Outer) -> Iterator[int32]:
    for c in o.inner.name:
        print(c)
        yield 1


def main() -> None:
    for v in gen_view(Holder()):
        print(v)


main()
