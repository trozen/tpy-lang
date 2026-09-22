# The for-source list literal OWNS its elements (it is container storage, not
# an alias), so a named `@nocopy` element is the located error -- the fence
# that keeps tests/cases/iterators/for_literal_ref_elements honest about what
# the loop variable aliases.
from tpy import int32, nocopy


@nocopy
class Tag:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def bump(self) -> None:
        self.n += 1


def main() -> None:
    t1 = Tag(1)
    t2 = Tag(2)
    for t in [t1, t2]:  # tpyc: error(/cannot copy non-copyable type 'Tag'/)
        t.bump()
    print(t1.n, t2.n)


main()
