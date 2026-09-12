from tpy import int32, Own, readonly
class Point:
    x: int32
    def __init__(self, x: int32) -> None:
        self.x = x
def mk() -> Own[readonly[Point]]:
    return Point(1)
def main() -> None:
    other = Point(5)
    p = mk()
    print(p.x)
    p = other
    print(p.x)
main()
