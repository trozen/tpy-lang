from tpy import int32, Own, readonly
class Point:
    x: int32
    def __init__(self, x: int32) -> None:
        self.x = x
from tplib import Box
def main() -> None:
    d = {1: Box(Point(1))}
    other = {2: Box(Point(2))}
    d = other
    print(len(d))
main()
