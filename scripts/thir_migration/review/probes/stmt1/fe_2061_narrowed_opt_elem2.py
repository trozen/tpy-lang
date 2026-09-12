from tpy import int32, Own, readonly
class Point:
    x: int32
    def __init__(self, x: int32) -> None:
        self.x = x
def go(u: list[Point | None] | int32) -> int32:
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
