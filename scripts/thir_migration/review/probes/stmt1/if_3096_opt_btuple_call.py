from tpy import Int32, Own, readonly
class Point:
    x: Int32
    def __init__(self, x: Int32) -> None:
        self.x = x
def mk() -> tuple[Int32, Point] | None:
    return None
class H:
    pair: tuple[Int32, Point] | None
    def __init__(self) -> None:
        self.pair = None
def go(c: bool, h: H) -> Int32:
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
