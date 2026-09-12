from tpy import int32, Ptr

class Point:
    x: int32
    y: int32

def identity_ptr(p: Ptr[Point]) -> Ptr[Point]:
    return p  # tpyc: ok (pointer value is copied)

def get_ptr_copy(p: Ptr[Point]) -> Ptr[Point]:
    local_ptr: Ptr[Point] = p
    return local_ptr  # tpyc: ok (pointer value is copied)

def main():
    pt: Point = Point()
    pt.x = 10
    pt.y = 20

    ptr: Ptr[Point] = pt
    ptr1: Ptr[Point] = identity_ptr(ptr)
    ptr2: Ptr[Point] = get_ptr_copy(ptr)

    print(ptr1.x)
    print(ptr2.y)

main()
