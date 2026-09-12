# super().__init__() covers only the MRO-first __init__ base (HasInitA);
# HasInitB still needs an explicit BaseN.__init__ call to satisfy coverage.
from tpy import int32


class HasInitA:
    a: int32

    def __init__(self, a: int32) -> None:
        self.a = a


class HasInitB:
    b: int32

    def __init__(self, b: int32) -> None:
        self.b = b


class Combined(HasInitA, HasInitB):
    def __init__(self, a: int32, b: int32) -> None:
        super().__init__(a)              # covers HasInitA (MRO-first)
        HasInitB.__init__(self, b)       # HasInitB still needs an explicit call


def main() -> None:
    c = Combined(int32(10), int32(20))
    print(c.a)
    print(c.b)


main()
