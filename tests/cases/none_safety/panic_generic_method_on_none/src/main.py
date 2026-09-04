# The null path of a GENERIC method call on an unproven Optional receiver: the
# template args ride the checked deref, so a None receiver panics there.
from tpy import Int32


class Bag:
    n: Int32

    def __init__(self, n: Int32) -> None:
        self.n = n

    def conv[T](self, x: T) -> T:
        return x


def generic_on_optional(b: Bag | None) -> Int32:
    return b.conv(3)  # tpyc: warning(/Potential None access/)


def main() -> None:
    print(generic_on_optional(Bag(1)))
    b: Bag | None = None
    print(generic_on_optional(b))


main()
