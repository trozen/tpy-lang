# make_default() portable default construction: inferred and explicit T, primitives and records
from tpy import int32, make_default

class Point:
    x: int32
    y: int32

    def __init__(self) -> None:
        self.x = 0
        self.y = 0

def test_explicit() -> None:
    a = make_default[int32]()
    print(a)

    b = make_default[str]()
    print(b)
    print(len(b))

    c = make_default[bool]()
    print(c)

def test_inferred() -> None:
    x: int32 = make_default()
    print(x)

    s: str = make_default()
    print(s)
    print(len(s))

def test_record() -> None:
    p = make_default[Point]()
    print(p.x)
    print(p.y)

def main() -> None:
    test_explicit()
    test_inferred()
    test_record()

main()
