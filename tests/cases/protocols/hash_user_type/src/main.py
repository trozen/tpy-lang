# User-defined __hash__ method and Hashable protocol conformance
from tpy import uint64, Hashable

class Point:
    x: int
    y: int
    def __init__(self, x: int, y: int) -> None:
        self.x = x
        self.y = y
    def __hash__(self) -> uint64:
        return hash(self.x) ^ hash(self.y)

def get_hash(x: Hashable) -> uint64:
    return hash(x)

def main() -> None:
    p1 = Point(1, 2)
    p2 = Point(1, 2)
    p3 = Point(3, 4)

    # Same fields produce the same hash
    print(hash(p1) == hash(p2))

    # Different fields (likely) produce different hashes
    print(hash(p1) != hash(p3))

    # Works through Hashable protocol parameter
    print(get_hash(p1) == get_hash(p2))
    print("ok")

main()
