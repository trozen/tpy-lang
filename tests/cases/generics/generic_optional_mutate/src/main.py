# Generic Optional returns a reference: mutating the returned object mutates the original.
from tpy import Int32

class Point:
    x: Int32
    y: Int32
    def __init__(self, x: Int32, y: Int32) -> None:
        self.x = x
        self.y = y

class Container[T]:
    _value: T
    _has: bool
    def __init__(self, value: T) -> None:
        self._value = value
        self._has = True
    def get(self) -> T | None:
        if self._has:
            return self._value
        return None

def main() -> None:
    c = Container[Point](Point(1, 2))
    p = c.get()
    if p is not None:
        print(p.x, p.y)
        # Mutate through the returned pointer -- should modify the original
        p.x = 10
        p.y = 20

    # Verify the original was mutated
    p2 = c.get()
    if p2 is not None:
        print(p2.x, p2.y)

    # Also test via dict.get()
    d: dict[str, Point] = {"a": Point(3, 4)}
    dp = d.get("a")
    if dp is not None:
        dp.x = 30
        dp.y = 40
    dp2 = d.get("a")
    if dp2 is not None:
        print(dp2.x, dp2.y)

main()
