# repr() and f-string interpolation work on union-typed values:
# the runtime variant overloads of __str__ / __repr__ visit the active
# alternative and recurse. Covers a value-type union (int32 | str), a
# 3-member union with a user record, and the !s / !r conversions.
from tpy import int32, Own


class Point:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x

    def __repr__(self) -> str:
        return f"Point({self.x})"


def make_iu() -> int32 | str:
    return int32(42)


def make_su() -> int32 | str:
    return "hello"


def make_3u() -> Own[int32 | str | Point]:
    return Point(7)


def main() -> None:
    a = make_iu()
    b = make_su()
    c = make_3u()
    print(repr(a))
    print(repr(b))
    print(repr(c))
    print(f"{a} and {b}")
    print(f"{b!s}")
    print(f"{b!r}")
    # Pointer-variant union (`c` holds a Point*): exercises the runtime
    # variant overload's pointer-deref branch through the f-string path.
    print(f"{c}")
    print(f"{c!s}")
    print(f"{c!r}")
    # Formattable type reaches __repr__ via runtime fallback; previously
    # rejected at sema, now accepted.
    n: int32 = 99
    print(f"{n!r}")


main()
