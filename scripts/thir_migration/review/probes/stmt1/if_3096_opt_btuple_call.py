from tpy import int32, Own, readonly
class Point:
    x: int32
    def __init__(self, x: int32) -> None:
        self.x = x
def mk() -> tuple[int32, Point] | None:
    return None
class H:
    pair: tuple[int32, Point] | None
    def __init__(self) -> None:
        self.pair = None
def go(c: bool, h: H) -> int32:
    if c:
        t = h.pair
    else:
        t = mk()
    if t is None:
        return 0
    return t[0]
def main() -> None:
    print(go(True, H()))
main()
