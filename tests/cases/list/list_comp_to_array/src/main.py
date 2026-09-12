# List comprehension resolves to Array when output size is known at compile time.
from tpy import int32, Array

def range_basic() -> None:
    squares = [x * x for x in range(5)]  # tpyc: type(/Array\[int32, 5\]/)
    for s in squares:
        print(s)

def range_transform() -> None:
    doubled = [x * 2 for x in range(4)]  # tpyc: type(/Array\[int32, 4\]/)
    print(len(doubled))
    print(doubled[0], doubled[3])

def range_empty() -> None:
    empty = [x for x in range(0)]  # tpyc: type(/Array\[int32, 0\]/)
    print(len(empty))

def range_two_arg() -> None:
    items = [x for x in range(2, 7)]  # tpyc: type(/Array\[int32, 5\]/)
    for i in items:
        print(i)

def array_source() -> None:
    src: Array[int32, 4] = [1, 2, 3, 4]
    doubled = [x * 2 for x in src]  # tpyc: type(/Array\[int32, 4\]/)
    for d in doubled:
        print(d)

def array_filter_fallback() -> None:
    src: Array[int32, 4] = [1, 2, 3, 4]
    evens = [x for x in src if x % 2 == 0]  # tpyc: type(/list\[int32\]/)
    for e in evens:
        print(e)

def range_three_arg() -> None:
    evens = [x for x in range(0, 10, 2)]  # tpyc: type(/Array\[int32, 5\]/)
    for e in evens:
        print(e)

def range_negative_step() -> None:
    countdown = [x for x in range(10, 0, -2)]  # tpyc: type(/Array\[int32, 5\]/)
    for c in countdown:
        print(c)

def range_empty_negative() -> None:
    empty = [x for x in range(0, 10, -1)]  # tpyc: type(/Array\[int32, 0\]/)
    print(len(empty))

def fallback_mutation() -> None:
    items = [x for x in range(3)]  # tpyc: type(/list\[int32\]/)
    items.append(99)
    for i in items:
        print(i)

def explicit_array_annotation() -> None:
    items: Array[int32, 5] = [x for x in range(5)]  # tpyc: type(/Array\[int32, 5\]/)
    for i in items:
        print(i)

def explicit_list_annotation() -> None:
    items: list[int32] = [x for x in range(3)]  # tpyc: type(/list\[int32\]/)
    for i in items:
        print(i)

range_basic()
range_transform()
range_empty()
range_two_arg()
array_source()
array_filter_fallback()
range_three_arg()
range_negative_step()
range_empty_negative()
explicit_array_annotation()
fallback_mutation()
explicit_list_annotation()
