# A ctor field initializer reading a union param `A | B` via member access
# (after isinstance narrowing) lowers correctly in the member-init list.
class A:
    x: int

    def __init__(self, x: int) -> None:
        self.x = x


class B:
    y: int

    def __init__(self, y: int) -> None:
        self.y = y


class Pick:
    val: int

    def __init__(self, p: A | B) -> None:
        self.val = p.x if isinstance(p, A) else p.y


def main() -> None:
    print(Pick(A(3)).val)
    print(Pick(B(4)).val)


main()
