from tpy import Ptr, ReadOnlyPtr, Int32

class Point:
    x: Int32
    y: Int32
    def __init__(self, x: Int32, y: Int32) -> None:
        self.x = x
        self.y = y

def get_ptr(p: Ptr[Point]) -> Ptr[Point]:
    return p

def main() -> None:
    a: Point = Point(Int32(1), Int32(2))
    b: Point = Point(Int32(3), Int32(4))

    # --- Branch: both sides non-null → intersection keeps non-null ---
    p: Ptr[Point] = Ptr(a)
    if a.x > Int32(0):
        p = Ptr(b)
    else:
        p = Ptr(a)
    # Both branches assign from Ptr(lvalue), so p is still non-null
    print(p.x)  # tpyc: ok

    # --- Branch: one side unknown → intersection clears ---
    q: Ptr[Point] = Ptr(a)
    if a.x > Int32(0):
        q = get_ptr(q)
    # Then-branch: unknown. Else-branch (implicit): still non-null.
    # Intersection → cleared.
    print(q.x)  # tpyc: ok

    # --- Loop: non-null before loop, not reassigned inside → preserved ---
    r: Ptr[Point] = Ptr(a)
    i: Int32 = Int32(0)
    while i < Int32(3):
        print(r.x)  # tpyc: ok
        i = i + Int32(1)

    # --- Loop: reassigned from unknown inside → cleared ---
    s: Ptr[Point] = Ptr(a)
    j: Int32 = Int32(0)
    while j < Int32(3):
        print(s.x)  # tpyc: ok
        s = get_ptr(s)
        j = j + Int32(1)
    # After loop: s was reassigned from unknown inside body
    print(s.x)  # tpyc: ok

main()
