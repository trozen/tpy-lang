# An isinstance-narrowed union subject passed to an `A | None` param takes
# the address-of lift over the extraction alias (`take_opt(&(__u))`) -- the
# statement-alias and compound-condition (inline get) forms. bump_opt
# mutates through the pointer and the caller observes it, pinning
# alias-not-copy at the boundary.
from tpy import int32


class A:
    x: int32

    def __init__(self, x: int32):
        self.x = x


class B:
    y: int32

    def __init__(self, y: int32):
        self.y = y


def take_opt(o: A | None) -> int32:
    if o is None:
        return 0
    return o.x


def bump_opt(o: A | None) -> None:
    if o is not None:
        o.x += 10


def read_narrowed(u: A | B) -> int32:
    if isinstance(u, A):
        return take_opt(u)
    return -1


def mutate_narrowed(u: A | B) -> int32:
    if isinstance(u, A):
        bump_opt(u)
        return u.x
    return -1


def inline_narrowed(u: A | B) -> int32:
    if isinstance(u, A) and take_opt(u) > 2:
        return 1
    return 0


def main() -> None:
    a = A(3)
    print(read_narrowed(a))
    print(mutate_narrowed(a))
    print(a.x)
    print(read_narrowed(B(9)))
    print(inline_narrowed(A(5)))
    print(inline_narrowed(A(1)))


main()
