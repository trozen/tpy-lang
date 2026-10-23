# A tuple literal holding a list, packed into `*args` of tuple elements, is
# refused: a tracked gap (BUGS.md#varargs-tuple-ref-element-copied).
from tpy import int32


def f(*xs: tuple[list[int32], int32]) -> None:
    for x in xs:
        print(x[1])


def main() -> None:
    a = [1, 2]
    t = (a, 1)
    # The subject: the `(a, 2)` element of the pack.
    f(t, (a, 2))  # tpyc: error(/not yet supported by C\+\+ code generation/)
    a.append(3)
    print(len(a))


main()
