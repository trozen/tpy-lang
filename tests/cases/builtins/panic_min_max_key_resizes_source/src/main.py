# A key that changes the size of the list min() / max() is walking raises
# RuntimeError: the result is a reference into that list, which the change
# may have moved (CPython keeps the object alive instead;
# BUGS.md#key-function-mutates-source). A key that grows the list past its
# capacity and shrinks it back (same size, new storage) raises the same way;
# the runtime self-check test_extreme_element.cpp pins that face.
class P:
    v: int

    def __init__(self, v: int) -> None:
        self.v = v


def shrink(ps: list[P], p: P) -> int:
    # the key reads its element before it resizes: nothing reads `p` after
    v = p.v
    if v == 3:
        del ps[0]
    return v


def main() -> None:
    ps = [P(1), P(5), P(3)]
    # the key resizes the list max() walks: the walk raises
    m = max(ps, key=lambda p: shrink(ps, p))
    m.v = 50
    print(m.v)


main()
