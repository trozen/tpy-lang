# Assigning to a read-only property (no setter) should error
from tpy import int32

class Foo:
    _x: int32

    def __init__(self, x: int32) -> None:
        self._x = x

    @property
    def x(self) -> int32:
        return self._x

def main() -> None:
    f = Foo(1)
    f.x = 2  # tpyc: error(/read-only/)

main()
