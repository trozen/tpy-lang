"""Tests all type coercions through the unified _apply_coercion path.

Each coercion is tested in multiple contexts:
- Variable declaration
- Assignment
- Return statement
- Function argument
"""
from tpy import Int32, Ptr, ReadOnlyPtr, Span, StaticList, Array

# --- Records for pointer coercion tests ---

class Point:
    x: Int32
    y: Int32

    def __init__(self, x: Int32, y: Int32) -> None:
        self.x = x
        self.y = y


# --- Helper functions that take specific types ---

def take_int32(n: Int32) -> Int32:
    return n

def take_ptr(p: Ptr[Point]) -> None:
    p.x = p.x + 1

def take_const_ptr(p: ReadOnlyPtr[Point]) -> Int32:
    return p.x + p.y

def take_point(p: Point) -> Int32:
    return p.x + p.y

def take_span(s: Span[Int32]) -> Int32:
    result: Int32 = 0
    i: Int32 = 0
    while i < len(s):
        result = result + s[i]
        i = i + 1
    return result


# --- BigInt -> Int32 coercion ---

def return_bigint_as_int32() -> Int32:
    big: int = 42
    return big  # BigInt -> Int32 in return


def test_bigint_to_int32() -> None:
    print("BigInt -> Int32 coercions:")

    # Variable declaration
    big: int = 100
    small: Int32 = big
    print(small)  # 100

    # Assignment
    big = 200
    small = big
    print(small)  # 200

    # Return
    result: Int32 = return_bigint_as_int32()
    print(result)  # 42

    # Function argument
    big = 300
    print(take_int32(big))  # 300


# --- Record -> Ptr coercion ---

def test_record_to_ptr() -> None:
    print("Record -> Ptr coercions:")

    pt: Point = Point(10, 20)

    # Variable declaration
    ptr: Ptr[Point] = pt
    print(ptr.x)  # 10

    # Assignment
    pt2: Point = Point(30, 40)
    ptr = pt2
    print(ptr.x)  # 30

    # Function argument (modifies through pointer)
    pt3: Point = Point(50, 60)
    take_ptr(pt3)
    print(pt3.x)  # 51 (modified by take_ptr)


# --- Record -> ReadOnlyPtr coercion ---

def test_record_to_const_ptr() -> None:
    print("Record -> ReadOnlyPtr coercions:")

    pt: Point = Point(5, 15)

    # Variable declaration
    cptr: ReadOnlyPtr[Point] = pt
    print(cptr.x)  # 5

    # Assignment
    pt2: Point = Point(25, 35)
    cptr = pt2
    print(cptr.x)  # 25

    # Function argument
    pt3: Point = Point(100, 200)
    print(take_const_ptr(pt3))  # 300


# --- Ptr -> Record coercion (dereference) ---

def return_record_from_ptr(p: Ptr[Point]) -> Point:
    return p  # Ptr -> Record in return


def test_ptr_to_record() -> None:
    print("Ptr -> Record coercions:")

    pt: Point = Point(7, 8)
    ptr: Ptr[Point] = pt

    # Variable declaration
    copy: Point = ptr
    print(copy.x)  # 7

    # Assignment
    pt2: Point = Point(9, 10)
    ptr2: Ptr[Point] = pt2
    copy = ptr2
    print(copy.x)  # 9

    # Return
    pt3: Point = Point(11, 12)
    returned: Point = return_record_from_ptr(pt3)
    print(returned.x)  # 11

    # Function argument
    pt4: Point = Point(13, 14)
    ptr4: Ptr[Point] = pt4
    print(take_point(ptr4))  # 27


# --- Ptr -> ReadOnlyPtr coercion ---

def take_const_ptr_val(p: ReadOnlyPtr[Point]) -> Int32:
    return p.x


def test_ptr_to_const_ptr() -> None:
    print("Ptr -> ReadOnlyPtr coercions:")

    pt: Point = Point(3, 4)
    ptr: Ptr[Point] = pt

    # Variable declaration
    cptr: ReadOnlyPtr[Point] = ptr
    print(cptr.x)  # 3

    # Assignment
    pt2: Point = Point(5, 6)
    ptr2: Ptr[Point] = pt2
    cptr = ptr2
    print(cptr.x)  # 5

    # Function argument
    pt3: Point = Point(7, 8)
    ptr3: Ptr[Point] = pt3
    print(take_const_ptr_val(ptr3))  # 7


# --- StaticList -> Span coercion ---

def test_staticlist_to_span() -> None:
    print("StaticList -> Span coercions:")

    sl: StaticList[Int32, 5] = StaticList[Int32, 5]()
    sl.append(1)
    sl.append(2)
    sl.append(3)

    # Function argument
    print(take_span(sl))  # 6


# --- Array -> Span coercion ---

def test_array_to_span() -> None:
    print("Array -> Span coercions:")

    arr: Array[Int32, 3] = [10, 20, 30]

    # Function argument
    print(take_span(arr))  # 60


# --- List -> Span coercion ---

def test_list_to_span() -> None:
    print("List -> Span coercions:")

    lst: list[Int32] = [100, 200, 300]

    # Function argument
    print(take_span(lst))  # 600


# --- Chained coercions: subscript -> Ptr ---

def test_subscript_to_ptr() -> None:
    print("Subscript -> Ptr coercions:")

    arr: Array[Point, 2] = [Point(1, 2), Point(3, 4)]

    # arr[0] is a Point, passed to function taking Ptr[Point]
    take_ptr(arr[0])
    print(arr[0].x)  # 2 (was 1, incremented by take_ptr)

    # arr[1] passed to ReadOnlyPtr param
    print(take_const_ptr(arr[1]))  # 7


# --- Run all tests ---

test_bigint_to_int32()
test_record_to_ptr()
test_record_to_const_ptr()
test_ptr_to_record()
test_ptr_to_const_ptr()
test_staticlist_to_span()
test_array_to_span()
test_list_to_span()
test_subscript_to_ptr()
