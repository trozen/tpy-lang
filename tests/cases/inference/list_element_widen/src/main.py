# Non-empty list element type widening via .append() and .insert()
from tpy import Int32, Int64

def test_int32_to_int64_append() -> None:
    xs = [Int32(1), Int32(2)]  # tpyc: type(list[Int64])
    xs.append(Int64(3))
    v: Int64 = xs[0]
    print(v)
    print(xs)

def test_literal_to_int64_append() -> None:
    xs = [1, 2]  # tpyc: type(list[Int64])
    xs.append(Int64(3))
    v: Int64 = xs[0]
    print(v)
    print(xs)

def test_literal_to_float_append() -> None:
    xs = [1, 2, 3]  # tpyc: type(list[float])
    xs.append(3.14)
    print(xs[3])

def test_int32_to_int64_insert() -> None:
    xs = [Int32(1), Int32(2)]  # tpyc: type(list[Int64])
    xs.insert(0, Int64(99))
    v: Int64 = xs[0]
    print(v)

def test_same_type_no_widen() -> None:
    xs = [Int32(1), Int32(2)]  # tpyc: type(list[Int32])
    xs.append(Int32(3))
    print(xs)

def test_multiple_widens() -> None:
    xs = [Int32(1)]  # tpyc: type(list[Int64])
    xs.append(Int32(2))
    xs.append(Int64(3))
    print(xs)

test_int32_to_int64_append()
test_literal_to_int64_append()
test_literal_to_float_append()
test_int32_to_int64_insert()
test_same_type_no_widen()
test_multiple_widens()
