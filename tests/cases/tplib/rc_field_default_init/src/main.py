# Regression: `self.field = Rc.new(...)` inside `__init__` -- using-decl
# scope. Used to fail C++ compile with `'Rc' was not declared in this scope`.
from tpy import int32
from tplib import Rc


class Val:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


class Holder:
    shared: Rc[Val]

    def __init__(self) -> None:
        self.shared = Rc.new(Val(0))


def main() -> None:
    h = Holder()
    print(h.shared.get().x)


main()
