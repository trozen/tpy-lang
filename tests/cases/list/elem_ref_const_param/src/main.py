# 8a.5: element-ref mutation is deferred until the borrower is actually written,
# so read-only element refs allow const T& for the source container param.
from tpy import Int32


class Point:
    x: Int32
    y: Int32
    def __init__(self, x: Int32, y: Int32) -> None:
        self.x = x
        self.y = y


def mutate_point(p: Point, val: Int32) -> None:
    p.x = val


def read_only_point(p: Point) -> Int32:
    return p.x


# --- Functions that only read through element ref: const T& ---

def read_elem_ref(items: list[Point]) -> Int32:
    """Element ref only read -> items is const T&."""
    v = items[Int32(0)]
    return v.x


def read_via_alias(items: list[Point]) -> Int32:
    """Alias of element ref only read -> items is const T&."""
    v = items[Int32(0)]
    w = v
    return w.x


def read_via_deep_alias(items: list[Point]) -> Int32:
    """Chain of aliases of element ref only read -> items is const T&."""
    v = items[Int32(0)]
    w = v
    x = w
    return x.x


def read_via_read_only_call(items: list[Point]) -> Int32:
    """Passes element ref to non-mutating callee -> items is const T&."""
    v = items[Int32(0)]
    return read_only_point(v)


# --- Functions that write through element ref: T& ---

def write_elem_ref(items: list[Point], val: Int32) -> None:
    """Element ref field write -> items is T&."""
    v = items[Int32(0)]
    v.x = val


def write_via_mutating_call(items: list[Point], val: Int32) -> None:
    """Passes element ref to mutating callee -> items is T&."""
    v = items[Int32(0)]
    mutate_point(v, val)


def write_via_alias(items: list[Point], val: Int32) -> None:
    """Alias of element ref then field write -> items is T&."""
    v = items[Int32(0)]
    w = v
    w.x = val


# --- Subscript write through element ref (nested list) ---

def read_nested(matrix: list[list[Int32]]) -> Int32:
    """Element ref of nested list only read -> matrix is const T&."""
    row = matrix[Int32(0)]
    return row[Int32(0)]


def write_nested(matrix: list[list[Int32]], val: Int32) -> None:
    """Subscript write through element ref -> matrix is T&."""
    row = matrix[Int32(0)]
    row[Int32(0)] = val


def main() -> None:
    pts: list[Point] = [Point(Int32(10), Int32(20)), Point(Int32(30), Int32(40))]
    print(read_elem_ref(pts))
    print(read_via_alias(pts))
    print(read_via_deep_alias(pts))
    print(read_via_read_only_call(pts))
    write_elem_ref(pts, Int32(99))
    print(pts[Int32(0)].x)
    write_via_mutating_call(pts, Int32(77))
    print(pts[Int32(0)].x)
    write_via_alias(pts, Int32(55))
    print(pts[Int32(0)].x)
    matrix: list[list[Int32]] = [[Int32(1), Int32(2)], [Int32(3), Int32(4)]]
    print(read_nested(matrix))
    write_nested(matrix, Int32(9))
    print(matrix[Int32(0)][Int32(0)])


main()
