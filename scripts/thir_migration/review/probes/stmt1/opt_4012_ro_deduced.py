from tpy import int32, Own, readonly
class Point:
    x: int32
    def __init__(self, x: int32) -> None:
        self.x = x
class Holder:
    value: Point | None
    def __init__(self) -> None:
        self.value = None
def go(h: readonly[Holder]) -> int32:
    v: Point | None = Point(1)
    if v is not None:
        print(v.x)
    v = h.value
    if v is not None:
        return v.x
    return 0
def main() -> None:
    print(go(Holder()))
main()
