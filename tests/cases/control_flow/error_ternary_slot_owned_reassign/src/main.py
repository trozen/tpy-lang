# A reassigned list local, rebound from a ternary of an existing list and a
# fresh one: the local is a pointer reseated per assignment, and the mixed
# select has no slot at a reseat, so it is refused rather than copied
# (BUGS.md#reference-ternary-position-gaps).
from tpy import int32, Own


def mk() -> Own[list[int32]]:
    return [7, 7, 7]


def f(a: list[int32], c: bool) -> None:
    x = [0] if c else [0, 0]
    x = mk() if not c else a  # tpyc: error(/decl\.reseat_source/)
    a.append(5)
    print(len(x))


def main() -> None:
    f([1], True)


main()
