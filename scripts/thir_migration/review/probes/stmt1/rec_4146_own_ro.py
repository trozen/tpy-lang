from tpy import Int32, Own, readonly
class Point:
    x: Int32
    def __init__(self, x: Int32) -> None:
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
