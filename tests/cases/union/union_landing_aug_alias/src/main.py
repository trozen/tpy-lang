# A reflected in-place fallback at a union of records updates the target's
# object in place, which an alias sees: the warned divergence from a rebind.
from tpy import Own, int32


class Base:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class Mid(Base):
    def __init__(self, n: int32) -> None:
        super().__init__(n)


class R2:
    # Orders R2 after Mid, which its operator returns by value
    # (BUGS.md#friend-operator-returns-later-subclass).
    last: Mid

    def __init__(self) -> None:
        self.last = Mid(0)

    def __radd__(self, other: Base | Mid) -> Own[Mid]:
        if isinstance(other, Mid):
            return Mid(other.n + 10)
        return Mid(other.n + 20)


def main() -> None:
    v = Mid(5)
    w = v
    # the warned in-place update: `w` sees 15 (CPython: 5)
    v += R2()  # tpyc: warning(/falls back to '__radd__'.*updated in place/)
    print("alias", w.n, v.n)


main()
