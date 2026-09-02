from tpy import Int32, Own, readonly
class Point:
    x: Int32
    def __init__(self, x: Int32) -> None:
        self.x = x
def pick(c: bool) -> Int32:
    p1 = Point(1)
    if c:
        q = p1
    else:
        return 0
    return q.x
def main() -> None:
    print(pick(True))
main()
