# Ptr non-null narrowing: skip deref_check after `is not None` guard
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

# if p is not None: p non-null in then-branch (field + method)
def test_if_not_none(p: Ptr[Point]) -> Int32:
    if p is not None:
        return p.sum()  # tpyc: non_null(p)
    return Int32(0)

# if p is None: return -- p non-null after early return
def test_is_none_early_return(p: Ptr[Point]) -> Int32:
    if p is None:
        return Int32(-1)
    return p.x  # tpyc: non_null(p)

# assert p is not None -- p non-null after assert
def test_assert(p: Ptr[Point]) -> Int32:
    assert p is not None
    return p.x  # tpyc: non_null(p)

# while p is not None -- p non-null inside loop body
def test_while(p: Ptr[Point]) -> None:
    while p is not None:
        print(p.x)  # tpyc: non_null(p)
        break

# while with reassignment from unknown inside body -- loses provenance after loop
def test_while_reassign(p: Ptr[Point]) -> None:
    while p is not None:
        print(p.x)  # tpyc: non_null(p)
        p = get_ptr(p)
        break
    # After loop: p was reassigned from unknown, non-null not guaranteed

# Ptr[readonly[...]]: same narrowing applies
def test_readonly_ptr(p: Ptr[readonly[Point]]) -> Int32:
    if p is not None:
        return p.x  # tpyc: non_null(p)
    return Int32(0)

# After branch merge without early return: non-null not guaranteed
def test_merge_no_guarantee(p: Ptr[Point]) -> Int32:
    if p is not None:
        pass
    return p.x  # tpyc: nullable(p)

def main() -> None:
    pt = Point(Int32(10), Int32(20))
    p: Ptr[Point] = Ptr(pt)
    cp: Ptr[readonly[Point]] = Ptr(pt)
    print(test_if_not_none(p))
    print(test_is_none_early_return(p))
    print(test_assert(p))
    test_while(p)
    test_while_reassign(p)
    print(test_readonly_ptr(cp))
    print(test_merge_no_guarantee(p))

main()
