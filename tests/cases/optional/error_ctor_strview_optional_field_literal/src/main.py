# A str literal into an `StrView | None` field in a constructor: the view inner
# needs the argument split, so the bare literal member init rejects.
from tpy import StrView


class H:
    s: StrView | None

    def __init__(self) -> None:
        self.s = "xy"  # tpyc: error(/ctor\.mil_field\.optional\.strliteral/)

    def has(self) -> bool:
        return self.s is not None


def main() -> None:
    print(H().has())


main()
