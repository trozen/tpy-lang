# Borrow contracts through generic free function and method calls.
# Tests: (a) mutation warning fires via generic call (return_borrows_from preserved
# across substitute_method_type_params), (b) return-through-local works for both
# non-generic and generic callers (is_param_derived_expr recognizes borrow contracts),
# (c) same for generic methods -- val_or_ref_t<T> emission and return-through-local
# via TpyMethodCall (addr_taken_roots handles self._items[0] -> "self").
from tpy import int32


class Point:
    x: int32
    y: int32

    def __init__(self, x: int32, y: int32) -> None:
        self.x = x
        self.y = y


def first[T](items: list[T]) -> T:
    return items[0]


# (a) Generic call registers ELEMENT borrow: mutation must warn.
def test_borrow_warn() -> None:
    pts = [Point(1, 2), Point(3, 4)]
    p = first(pts)
    pts.append(Point(5, 6))  # tpyc: warning(/Mutation of 'pts' while borrowed/)
    print(len(pts))           # 3


# (b) Return-through-local: non-generic caller -- p borrows from items via 8b contract.
def get_first_concrete(items: list[Point]) -> Point:
    p = first(items)
    return p  # tpyc: ok


# (c) Return-through-local: generic caller -- p is val_or_ref_t<T>, param-derived.
def get_first_generic[T](items: list[T]) -> T:
    p = first(items)
    return p  # tpyc: ok


def test_return_through_local() -> None:
    pts = [Point(1, 2), Point(3, 4)]
    a = get_first_concrete(pts)
    b = get_first_generic(pts)
    print(a.x)   # 1
    print(b.x)   # 1


# (d) Generic method: addr_taken_roots(self._items[0]) -> "self", so
# return_borrows_from = {-1} (borrows from self).
class Box[T]:
    _items: list[T]

    def __init__(self, items: list[T]) -> None:
        self._items = items

    def first(self) -> T:
        return self._items[0]  # borrows from self


# (d1) val_or_ref_t<T> reference semantics: mutation through p affects box's data.
def test_method_ref_semantics() -> None:
    pts: list[Point] = [Point(1, 2), Point(3, 4)]
    box = Box(pts)
    p = box.first()  # tpyc: type(Point)  -- val_or_ref_t<Point>: Point& for records
    p.x = 99
    print(box._items[0].x)   # 99: p is a reference into box._items, not a copy


# (d2) Return-through-local: generic method caller (TpyMethodCall in is_param_derived_expr).
def get_first_from_box[T](box: Box[T]) -> T:
    p = box.first()
    return p  # tpyc: ok


def test_method_return_through_local() -> None:
    pts: list[Point] = [Point(1, 2), Point(3, 4)]
    box = Box(pts)
    result = get_first_from_box(box)
    print(result.x)   # 1


def main() -> None:
    test_borrow_warn()
    test_return_through_local()
    test_method_ref_semantics()
    test_method_return_through_local()


main()
