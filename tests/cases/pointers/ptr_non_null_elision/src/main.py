from tpy import Ptr, Int32, readonly

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
    print(p.x)
    print(p.sum())

def main() -> None:
    pt: Point = Point(Int32(10), Int32(20))
    # Local Ptr from lvalue: provably non-null, skip null check
    p: Ptr[Point] = Ptr(pt)
    print(p.x)
    print(p.y)
    print(p.sum())
    # Local Ptr[readonly[...]] from lvalue: provably non-null, skip null check
    cp: Ptr[readonly[Point]] = Ptr(pt)
    print(cp.x)
    # Coercion: Ptr[T] -> Ptr[readonly[T]] preserves non-null provenance
    cp2: Ptr[readonly[Point]] = p
    print(cp2.y)
    # Propagated provenance: q copies from known non-null p
    q: Ptr[Point] = p
    print(q.y)
    # Unknown source: function return clears non-null provenance
    r: Ptr[Point] = get_ptr(p)
    print(r.x)
    # Reassignment from unknown clears provenance
    p = get_ptr(q)
    print(p.x)
    # Re-establish provenance
    pt2: Point = Point(Int32(30), Int32(40))
    p = Ptr(pt2)
    print(p.x)
    # Pass to function (param has unknown provenance inside)
    read_via_param(p)

main()
