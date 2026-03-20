# Borrow tracking through field-path expressions (self.items, obj.field)
from tpy import Int32, Ptr, take_ptr

class Point:
    x: Int32
    y: Int32
    def __init__(self, x: Int32, y: Int32) -> None:
        self.x = x
        self.y = y

class Container:
    items: list[Point]
    def __init__(self) -> None:
        self.items = [Point(1, 2), Point(3, 4)]

    def iter_then_mutate(self) -> None:
        """Iterating over self.items while mutating: should warn."""
        for p in self.items:
            self.items.append(Point(p.x, p.y))  # tpyc: warning(/while iterating/)
            break

    def ptr_then_mutate(self) -> None:
        """Ptr into self.items element + structural mutation: should warn."""
        ptr = take_ptr(self.items[0])
        self.items.append(Point(5, 6))  # tpyc: warning(/'append'.*invalidate/)
        print(len(self.items))

    def safe_subscript_assign(self) -> None:
        """Ptr into self.items + in-place subscript assign: no reallocation, safe."""
        ptr = take_ptr(self.items[0])
        self.items[0] = Point(9, 9)  # tpyc: ok
        print(len(self.items))

    def aug_assign_field_container(self) -> None:
        """Aug-assign on self.items while borrowed: should warn."""
        ptr = take_ptr(self.items[0])
        self.items += [Point(5, 6)]  # tpyc: warning(/while borrowed/)
        print(len(self.items))

    def field_reassign_while_borrowed(self) -> None:
        """Reassigning self.items while borrowed: should warn."""
        ptr = take_ptr(self.items[0])
        self.items = [Point(9, 9)]  # tpyc: warning(/while borrowed/)
        print(len(self.items))

class Holder:
    point: Point
    def __init__(self, p: Point) -> None:
        self.point = p

def ptr_to_field() -> None:
    """Ptr to obj.field tracks borrow on 'h.point', not just 'h'."""
    h = Holder(Point(1, 2))
    ptr = take_ptr(h.point)
    h.point = Point(9, 9)  # tpyc: warning(/while borrowed/)
    print(h.point.x)

def ptr_to_field_no_conflict() -> None:
    """Ptr to obj.field -- mutating a different field is safe."""
    h = Holder(Point(1, 2))
    ptr = take_ptr(h.point)
    # Mutating h itself (not h.point) would warn because the borrow is on "h.point"
    # but there's no mutation of h.point here, only a read.
    print(ptr.x)

def external_field_path() -> None:
    """Field-path borrow on a local variable (not self)."""
    c = Container()
    ptr = take_ptr(c.items[0])
    c.items.append(Point(7, 8))  # tpyc: warning(/'append'.*invalidate/)
    print(len(c.items))

def reassign_clears_borrow() -> None:
    """Reassigning the root variable clears field-path borrows."""
    c = Container()
    ptr = take_ptr(c.items[0])
    c = Container()
    c.items.append(Point(7, 8))  # tpyc: ok
    print(len(c.items))

def main() -> None:
    c = Container()
    c.iter_then_mutate()
    c.ptr_then_mutate()
    c.safe_subscript_assign()
    c.aug_assign_field_container()
    c.field_reassign_while_borrowed()
    ptr_to_field()
    ptr_to_field_no_conflict()
    external_field_path()
    reassign_clears_borrow()

main()
