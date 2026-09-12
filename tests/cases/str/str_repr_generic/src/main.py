# Test __str__/__repr__ on generic records with Stringable bound
from tpy import int32, Stringable

class Pair:
    x: int32
    y: int32

    def __init__(self, x: int32, y: int32) -> None:
        self.x = x
        self.y = y

    def __str__(self) -> str:
        return f"({self.x}, {self.y})"

class Wrapper[T: Stringable]:
    value: T

    def __init__(self, value: T) -> None:
        self.value = value

    def __str__(self) -> str:
        return f"Wrapper({self.value})"

    def __repr__(self) -> str:
        return f"Wrapper(value={self.value})"

def main() -> None:
    wp: Wrapper[Pair] = Wrapper(Pair(1, 2))
    print(str(wp))
    print(repr(wp))
    print(wp)
    print(f"val = {wp}")
    print(f"debug: {wp!r}")

main()
