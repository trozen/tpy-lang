# raise X(a) where the ctor param is a union `A | B` routes through the shared
# loop's union arm. Field-store copy of the payload is intended (warned).
class A:
    x: int

    def __init__(self, x: int) -> None:
        self.x = x


class B:
    y: int

    def __init__(self, y: int) -> None:
        self.y = y


class UErr(Exception):
    payload: A | B

    def __init__(self, p: A | B) -> None:
        super().__init__("u")
        self.payload = p


def main() -> None:
    try:
        raise UErr(A(7))
    except UErr as e:
        match e.payload:
            case A() as av:
                print(av.x)
            case B() as bv:
                print(bv.y)


main()
