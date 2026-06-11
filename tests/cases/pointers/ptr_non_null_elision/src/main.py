from tpy import Ptr, Int32, readonly, take_ptr

class Point:
    x: Int32
    y: Int32
    def __init__(self, x: Int32, y: Int32) -> None:
        self.x = x
        self.y = y
    def sum(self) -> Int32:
        return self.x + self.y

def get_ptr(p: Ptr[Point]) -> Ptr[Point]:
    return p

# Function parameter: provenance unknown, must null-check
def read_via_param(p: Ptr[Point]) -> None:
    print(p.x)  # tpyc: nullable(p)
    print(p.sum())  # tpyc: non_null(p)

def main() -> None:
    pt: Point = Point(Int32(10), Int32(20))
    # Local Ptr from lvalue: provably non-null, skip null check
    p: Ptr[Point] = pt
    print(p.x)  # tpyc: non_null(p)
    print(p.y)  # tpyc: non_null(p)
    print(p.sum())  # tpyc: non_null(p)
    # Local Ptr[readonly[...]] from lvalue: provably non-null, skip null check
    cp: Ptr[readonly[Point]] = pt
    print(cp.x)  # tpyc: non_null(cp)
    # Coercion: Ptr[T] -> Ptr[readonly[T]] preserves non-null provenance
    cp2: Ptr[readonly[Point]] = p
    print(cp2.y)  # tpyc: non_null(cp2)
    # Propagated provenance: q copies from known non-null p
    q: Ptr[Point] = p
    print(q.y)  # tpyc: non_null(q)
    # Unknown source: function return clears non-null provenance
    r: Ptr[Point] = get_ptr(p)
    print(r.x)  # tpyc: nullable(r)
    # Reassignment from unknown clears provenance
    p = get_ptr(q)
    print(p.x)  # tpyc: nullable(p)
    # Re-establish provenance
    pt2: Point = Point(Int32(30), Int32(40))
    p = take_ptr(pt2)
    print(p.x)  # tpyc: non_null(p)
    # Pass to function (param has unknown provenance inside)
    read_via_param(p)

main()
