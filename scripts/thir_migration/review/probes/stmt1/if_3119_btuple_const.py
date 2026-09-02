from tpy import Int32, Own, readonly
class Point:
    x: Int32
    def __init__(self, x: Int32) -> None:
        self.x = x
class H:
    pair: tuple[Int32, Point]
    def __init__(self) -> None:
        self.pair = (1, Point(2))
def go(c: bool, h: readonly[H], h2: H) -> Int32:
    if c:
        t = h.pair
    else:
        t = h2.pair
    return t[0]
def main() -> None:
    print(go(True, H(), H()))
main()
