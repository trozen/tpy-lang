# Generic Optional[T] returns T* (pointer into stored data) for zero-copy
# reference semantics. Caller matches template's T* representation.
from tpy import Own

class Point:
    x: int
    y: int
    def __init__(self, x: int, y: int) -> None:
        self.x = x
        self.y = y

    def sum(self) -> int:
        return self.x + self.y

class Container[T]:
    _value: T
    _has: bool

    def __init__(self, value: Own[T]) -> None:
        self._value = value
        self._has = True

    def get(self) -> T | None:
        if self._has:
            return self._value
        return None

def accept_opt(p: Point | None) -> None:
    if p is not None:
        print(p.x)

def main() -> None:
    c = Container[Point](Point(1, 2))
    p = c.get()
    # Field access on narrowed std::optional
    if p is not None:
        print(p.x)
        print(p.y)
        # Method call on narrowed std::optional
        print(p.sum())

    # Pass generic Optional return to function expecting Optional[Point]
    p2 = c.get()
    accept_opt(p2)

    # Also test with value type (should still work)
    c2 = Container[int](42)
    v = c2.get()
    if v is not None:
        print(v)

main()
