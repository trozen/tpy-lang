from tpy import Int32, Own, readonly
class Point:
    x: Int32
    def __init__(self, x: Int32) -> None:
        self.x = x
def main() -> None:
    for i in range(2):
        if i == 0:
            p = Point(1)
        else:
            p = Point(2)
        print(p.x)
main()
