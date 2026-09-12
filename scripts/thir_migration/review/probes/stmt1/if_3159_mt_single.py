from tpy import int32, Own, readonly
class Point:
    x: int32
    def __init__(self, x: int32) -> None:
        self.x = x
def pick(c: bool) -> int32:
    p1 = Point(1)
    if c:
        q = p1
    else:
        return 0
    return q.x
def main() -> None:
    print(pick(True))
main()
