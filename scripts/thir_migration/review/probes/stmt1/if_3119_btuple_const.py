from tpy import int32, Own, readonly
class Point:
    x: int32
    def __init__(self, x: int32) -> None:
        self.x = x
class H:
    pair: tuple[int32, Point]
    def __init__(self) -> None:
        self.pair = (1, Point(2))
def go(c: bool, h: readonly[H], h2: H) -> int32:
    if c:
        t = h.pair
    else:
        t = h2.pair
    return t[0]
def main() -> None:
    print(go(True, H(), H()))
main()
