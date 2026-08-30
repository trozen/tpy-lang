# `case A() | None:` over a nullable union. Union dispatch selects an
# alternative by its member class's variant index, and `None` names no member
# class, so sema rejects the alternative -- `case None:` needs its own arm.


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
