from tpy import Ptr, Int32, readonly, take_ptr

class Point:
    x: Int32
    y: Int32
    def __init__(self, x: Int32, y: Int32) -> None:
        self.x = x
        self.y = y

def read_via_ptr(p: Ptr[Point]) -> None:
    print(p.x)
    print(p.y)

def read_via_constptr(p: Ptr[readonly[Point]]) -> None:
    print(p.x)
    print(p.y)

def test_null_constructors() -> None:
    p1: Ptr[None] = Ptr[None]()
    p2: Ptr[readonly[None]] = Ptr[readonly[None]]()
    p3: Ptr[Int32] = Ptr[Int32]()
    p4: Ptr[readonly[Int32]] = Ptr[readonly[Int32]]()
    print("null ok")

def test_ptr_explicit() -> None:
    pt: Point = Point(10, 20)
    pp: Ptr[Point] = take_ptr(pt)
    read_via_ptr(pp)

def test_ptr_inferred() -> None:
    pt: Point = Point(30, 40)
    pp: Ptr[Point] = take_ptr(pt)
    read_via_ptr(pp)

def test_constptr_explicit() -> None:
    pt: Point = Point(50, 60)
    cp: Ptr[readonly[Point]] = take_ptr(pt)
    read_via_constptr(cp)

def test_constptr_inferred() -> None:
    pt: Point = Point(70, 80)
    cp: Ptr[readonly[Point]] = take_ptr(pt)
    read_via_constptr(cp)

def test_ptr_write() -> None:
    pt: Point = Point(1, 2)
    pp: Ptr[Point] = take_ptr(pt)
    pp.x = Int32(99)
    print(pt.x)

test_null_constructors()
test_ptr_explicit()
test_ptr_inferred()
test_constptr_explicit()
test_constptr_inferred()
test_ptr_write()
