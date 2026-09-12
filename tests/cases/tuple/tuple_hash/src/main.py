# Tuple hashing: hash() on tuples, tuple as dict key, @nocopy elements
from __future__ import annotations
from tpy import int32, uint64, nocopy

def main() -> None:
    t = (1, 2, 3)
    h = hash(t)
    print(h > 0 or h <= 0)

    # Equal tuples produce equal hashes
    a = (10, "hello")
    b = (10, "hello")
    print(hash(a) == hash(b))

    # Tuple as dict key
    d: dict[tuple[int32, int32], str] = {}
    d[(int32(1), int32(2))] = "one-two"
    d[(int32(3), int32(4))] = "three-four"
    print(d[(int32(1), int32(2))])
    print(d[(int32(3), int32(4))])
    print(len(d))

    # @nocopy element -- hash must not copy
    test_nocopy_hash()

    # hash(tuple) inside __hash__ -- the common Python idiom
    test_hash_delegation()

@nocopy
class Key:
    val: int32

    def __init__(self, v: int32) -> None:
        self.val = v

    def __hash__(self) -> uint64:
        return uint64(self.val)

    def __eq__(self, other: Key) -> bool:
        return self.val == other.val

def test_nocopy_hash() -> None:
    t = (Key(10), Key(20))
    u = (Key(10), Key(20))
    print(hash(t) == hash(u))

class Point:
    x: int32
    y: int32

    def __init__(self, x: int32, y: int32) -> None:
        self.x = x
        self.y = y

    def __hash__(self) -> uint64:
        return hash((self.x, self.y))

    def __eq__(self, other: Point) -> bool:
        return self.x == other.x and self.y == other.y

@nocopy
class Edge:
    a: Key
    b: Key

    def __init__(self, x: int32, y: int32) -> None:
        self.a = Key(x)
        self.b = Key(y)

    def __hash__(self) -> uint64:
        return hash((self.a, self.b))

    def __eq__(self, other: Edge) -> bool:
        return (self.a, self.b) == (other.a, other.b)

def test_hash_delegation() -> None:
    # Value-type fields
    p1 = Point(1, 2)
    p2 = Point(1, 2)
    print(hash(p1) == hash(p2))

    # @nocopy fields -- hash must use const refs, no copies
    e1 = Edge(3, 4)
    e2 = Edge(3, 4)
    print(hash(e1) == hash(e2))

main()
