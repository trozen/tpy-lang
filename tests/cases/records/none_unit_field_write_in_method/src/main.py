# A `None`-typed field assigned `None` OUTSIDE the constructor: the method
# body takes the residual field-write family's storage literal.
from tpy import int32


class Field:
    slot: None
    n: int32

    def __init__(self) -> None:
        self.slot = None
        self.n = 0

    def reset(self) -> None:
        # The same write as the constructor's, but in a method body.
        self.slot = None
        self.n += 1


def main() -> None:
    f = Field()
    f.reset()
    f.reset()
    print(f.n)


main()
