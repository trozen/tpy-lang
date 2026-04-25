# super().__init__() covers only the MRO-first __init__ base (HasInitA);
# HasInitB still needs an explicit BaseN.__init__ call to satisfy coverage.
from tpy import Int32


class HasInitA:
    a: Int32

    def __init__(self, a: Int32) -> None:
        self.a = a


class HasInitB:
    b: Int32

    def __init__(self, b: Int32) -> None:
        self.b = b


class Combined(HasInitA, HasInitB):
    def __init__(self, a: Int32, b: Int32) -> None:
        super().__init__(a)              # covers HasInitA (MRO-first)
        HasInitB.__init__(self, b)       # HasInitB still needs an explicit call


def main() -> None:
    c = Combined(Int32(10), Int32(20))
    print(c.a)
    print(c.b)


main()
