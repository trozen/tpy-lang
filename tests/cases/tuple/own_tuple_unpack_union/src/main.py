# Tuple-unpacking a return of `tuple[Own[A|B], ...]`: the source tuple
# holds value-variant elements (`std::variant<A, B>`) because Own pushes
# storage form per element. The unpack must declare the unpacked variant
# local as pointer-variant and lift via to_ptr_variant, otherwise
# subsequent uses that expect `variant<A*, B*>` fail to convert.
from tpy import Int32, Own


class A:
    x: Int32
    def __init__(self, x: Int32) -> None:
        self.x = x


class B:
    y: Int32
    def __init__(self, y: Int32) -> None:
        self.y = y


def pair() -> tuple[Own[A | B], Int32]:
    return (A(42), Int32(99))


def borrow(u: A | B) -> Int32:
    if isinstance(u, A):
        return u.x
    if isinstance(u, B):
        return u.y
    return Int32(0)


def main() -> None:
    p, n = pair()
    print(borrow(p))
    print(n)


main()
