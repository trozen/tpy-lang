# `case A() | None:` over a nullable union is rejected today: the union
# or-pattern tiers only stack member-CLASS labels, so `case None:` needs its
# own arm. A limitation, not a rule -- `None` has a variant index like any
# member; see BUGS.md#or-pattern-none-alt-union.


class A:
    a: int

    def __init__(self, a: int) -> None:
        self.a = a


class B:
    b: int

    def __init__(self, b: int) -> None:
        self.b = b


def describe(v: A | B | None) -> str:
    match v:
        case A() | None:  # tpyc: error(/unsupported alternative in an or-pattern over a union subject/)
            return "a-or-none"
        case B():
            return "b"


def main() -> None:
    pass


main()
