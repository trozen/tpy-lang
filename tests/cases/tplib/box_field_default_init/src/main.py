# Regression: `self.field = Box(...)` inside `__init__` -- Box analog of
# rc_field_default_init.
from tpy import Int32
from tplib import Box


class Val:
    x: Int32

    def __init__(self, x: Int32) -> None:
        self.x = x


class Holder:
    boxed: Box[Val]

    def __init__(self) -> None:
        self.boxed = Box(Val(7))


def main() -> None:
    h = Holder()
    print(h.boxed.get().x)


main()
