# A literal-seeded list widens its element across integer widths through
# .append() and .insert(); a list seeded with typed values keeps their type.
from tpy import int32, int64

def test_literal_to_int64_append() -> None:
    xs = [1, 2]  # tpyc: type(list[int64])
    xs.append(int64(3))
    v: int64 = xs[0]
    print(v)
    print(xs)

def test_literal_to_int64_insert() -> None:
    xs = [1, 2]  # tpyc: type(list[int64])
    xs.insert(0, int64(99))
    v: int64 = xs[0]
    print(v)

def test_same_type_no_widen() -> None:
    # Typed values decide the element at the first binding; a value of the
    # same type and a fitting literal store nothing new.
    xs = [int32(1), int32(2)]  # tpyc: type(list[int32])
    xs.append(int32(3))
    xs.append(4)
    print(xs)

def test_multiple_widens() -> None:
    xs = [1]  # tpyc: type(list[int64])
    xs.append(int32(2))
    xs.append(int64(3))
    print(xs)

test_literal_to_int64_append()
test_literal_to_int64_insert()
test_same_type_no_widen()
test_multiple_widens()
