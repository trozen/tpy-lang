# A generator-valued PROPERTY as a for-each iterable rejects: the getter call's
# own result gate refuses a generator return.
# The gate is PROPERTY-SPECIFIC and the reject is a shipped limitation, not a
# rule: the identical generator METHOD iterable compiles, because the
# member-gen-call classifier passes `generator_ok=True` at the iterable position
# and the getter read does not reach it. Removing it is filed in TODO.md
# ("A generator-returning `@property` is refused where the spelled method twin
# is admitted"); until then, spell the accessor as a method.
from typing import Iterator
from tpy import int32


class Bag:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    @property
    def items(self) -> Iterator[int32]:
        i = 0
        while i < self.n:
            yield i
            i = i + 1


def main() -> None:
    b = Bag(2)
    for v in b.items:  # tpyc: error(/method.fi_kind/)
        print(v)


main()
