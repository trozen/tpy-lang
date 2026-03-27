# sorted() rejects types that don't satisfy Comparable

class Point:
    def __init__(self, x: int, y: int) -> None:
        self.x = x
        self.y = y

def main() -> None:
    pts = [Point(1, 2), Point(3, 4)]
    print(sorted(pts))  # tpyc: error(/does not satisfy 'Comparable'/)

main()
