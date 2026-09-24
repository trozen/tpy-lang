# A list local that owns its storage, reassigned from a ternary of an
# existing list and a fresh one: the reassignment would copy the existing
# list where CPython rebinds the name to it, so it is refused rather than
# copied (BUGS.md#reference-ternary-position-gaps).
from tpy import int32, Own


def mk() -> Own[list[int32]]:
    return [7, 7, 7]


def f(a: list[int32], c: bool) -> None:
    x = [0] if c else [0, 0]
    x = mk() if not c else a  # tpyc: error(/expr\.ifexpr/)
    a.append(5)
    print(len(x))


def main() -> None:
    f([1], True)


main()
