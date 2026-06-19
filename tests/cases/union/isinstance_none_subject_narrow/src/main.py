# isinstance narrowing on a union subject that CONTAINS None: the else branch
# narrows to the lone remaining None member (no value to extract). Covers tuple
# and inline-union forms over scalar and reference subjects.
class A:
    def __init__(self) -> None:
        pass


class B:
    def __init__(self) -> None:
        pass


def scalar_tuple(v: int | str | None) -> str:
    if isinstance(v, (int, str)):
        return "present"
    return "none"


def scalar_inline(v: int | str | None) -> str:
    if isinstance(v, int | str):
        return "present"
    return "none"


def ref_tuple(v: A | B | None) -> str:
    if isinstance(v, (A, B)):
        return "ab"
    return "none"


def main() -> None:
    a: int | str | None = 5
    print(scalar_tuple(a))
    print(scalar_inline(a))
    n: int | str | None = None
    print(scalar_tuple(n))
    print(scalar_inline(n))
    x: A | B | None = A()
    print(ref_tuple(x))
    y: A | B | None = None
    print(ref_tuple(y))


main()
