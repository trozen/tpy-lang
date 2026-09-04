# A nested def capturing a NARROWED union variable. Reads of it rename to the
# enclosing extraction alias, which the capture list does not carry, so the
# lambda body would reference a name it never captured.
from tpy import Int32


class A:
    n: Int32

    def __init__(self) -> None:
        self.n = 1


class B:
    m: Int32

    def __init__(self) -> None:
        self.m = 2


def size(u: A | B) -> Int32:
    if isinstance(u, A):
        return u.n
    return 0


def go(u: A | B) -> Int32:
    if isinstance(u, A):
        def f() -> Int32:  # tpyc: error(/not yet supported.*nesteddef.narrowed_capture/)
            return size(u)

        return f()
    return 0


def main() -> None:
    print(go(A()))


main()
