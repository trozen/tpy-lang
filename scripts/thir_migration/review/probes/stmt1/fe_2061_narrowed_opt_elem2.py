from tpy import Int32, Own, readonly
class Point:
    x: Int32
    def __init__(self, x: Int32) -> None:
        self.x = x
def go(u: list[Point | None] | Int32) -> Int32:
    t = 0
    if isinstance(u, list):
        for p in u:
            if p is not None:
                t += p.x
    return t
def main() -> None:
    xs: list[Point | None] = [Point(1), None]
    print(go(xs))
main()
