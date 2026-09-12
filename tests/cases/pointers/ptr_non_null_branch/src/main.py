from tpy import Ptr, int32, take_ptr

class Point:
    x: int32
    y: int32
    def __init__(self, x: int32, y: int32) -> None:
        self.x = x
        self.y = y

def get_ptr(p: Ptr[Point]) -> Ptr[Point]:
    return p

def main() -> None:
    a: Point = Point(int32(1), int32(2))
    b: Point = Point(int32(3), int32(4))

    # --- Branch: both sides non-null → intersection keeps non-null ---
    p: Ptr[Point] = a
    if a.x > int32(0):
        p = b
    else:
        p = a
    # Both branches assign from take_ptr(lvalue), so p is still non-null
    print(p.x)  # tpyc: ok non_null(p)

    # --- Branch: one side unknown → intersection clears ---
    q: Ptr[Point] = a
    if a.x > int32(0):
        q = get_ptr(q)
    # Then-branch: unknown. Else-branch (implicit): still non-null.
    # Intersection → cleared.
    print(q.x)  # tpyc: ok nullable(q)

    # --- Loop: non-null before loop, not reassigned inside → preserved ---
    r: Ptr[Point] = a
    i: int32 = int32(0)
    while i < int32(3):
        print(r.x)  # tpyc: ok non_null(r)
        i = i + int32(1)

    # --- Loop: reassigned from unknown inside → cleared ---
    s: Ptr[Point] = a
    j: int32 = int32(0)
    while j < int32(3):
        print(s.x)  # tpyc: ok nullable(s)
        s = get_ptr(s)
        j = j + int32(1)
    # After loop: s was reassigned from unknown inside body
    print(s.x)  # tpyc: ok nullable(s)

main()
