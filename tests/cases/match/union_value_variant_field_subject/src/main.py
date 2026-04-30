# Regression: match directly on a value-variant union field of `self`.
# Before the fix, the codegen used the type's *primary* repr (pointer-variant
# for unions of records) and emitted `*std::get<I>(__match_subject)`, which
# fails to compile because the field's actual storage is value-variant.
class A:
    x: str
    def __init__(self, x: str) -> None:
        self.x = x


class B:
    y: str
    def __init__(self, y: str) -> None:
        self.y = y


class W:
    f: A | B

    def __init__(self, f: A | B) -> None:
        self.f = f

    def get_label(self) -> str:
        match self.f:
            case A(x=v):
                return "a:" + v
            case B(y=v):
                return "b:" + v


def main() -> None:
    print(W(A("hi")).get_label())
    print(W(B("hello")).get_label())


main()
