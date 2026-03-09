# Empty list [] and list() infer element type from subsequent usage
from tpy import Int32, Int64, Own

def test_append() -> None:
    xs = []  # tpyc: type(list[Int32])
    xs.append(42)
    print(xs)

def test_list_ctor() -> None:
    xs = list()  # tpyc: type(list[Int32])
    xs.append(42)
    print(xs)

def test_insert() -> None:
    xs = []  # tpyc: type(list[Int32])
    xs.insert(0, 99)
    print(xs)

def test_multiple_append() -> None:
    xs = []  # tpyc: type(list[Int32])
    xs.append(1)
    xs.append(2)
    xs.append(3)
    print(xs)

def test_numeric_widen() -> None:
    xs = []  # tpyc: type(list[Int64])
    xs.append(Int32(1))
    xs.append(Int64(2))
    print(xs)

def test_return_context() -> Own[list[int]]:
    xs = []  # tpyc: type(list[int])
    xs.append(1)
    return xs

def takes_list(items: list[int]) -> None:
    for x in items:
        print(x)

def test_param_context() -> None:
    xs = []  # tpyc: type(list[int])
    xs.append(10)
    takes_list(xs)

def test_param_overrides_inferred() -> None:
    xs = []  # tpyc: type(list[int])
    xs.append(Int32(1))
    takes_list(xs)

def test_alias_inference() -> None:
    xs = []  # tpyc: type(list[Int32])
    ys = xs
    ys.append(42)
    print(xs)
    print(ys)

test_append()
test_list_ctor()
test_insert()
test_multiple_append()
test_numeric_widen()
result = test_return_context()
print(result)
test_param_context()
test_param_overrides_inferred()
test_alias_inference()
