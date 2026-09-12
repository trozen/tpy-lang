# Float literals in containers adapt to context (FloatLiteralType inference)
from tpy import float32

def test_list_inferred() -> None:
    xs = [1.5, 2.5, 3.5]  # tpyc: type(list[float])
    xs.append(4.5)
    print(xs)

def test_list_annotated_float32() -> None:
    xs: list[float32] = [1.0, 2.0, 3.0]  # tpyc: type(list[float32])
    print(xs)

def test_dict_inferred() -> None:
    d = {"a": 1.5, "b": 2.5}  # tpyc: type(dict[str, float])
    print(d)

def test_set_inferred() -> None:
    s = {1.5, 2.5, 3.5}  # tpyc: type(set[float])
    print(s)

def test_ternary_float_literal() -> None:
    x: float32 = float32(1.0)
    y = x if True else 2.0  # tpyc: type(float32)
    print(y)

def test_annotated_float64() -> None:
    b: float = 5.0  # tpyc: type(float)
    print(b)

test_list_inferred()
test_list_annotated_float32()
test_dict_inferred()
test_set_inferred()
test_ternary_float_literal()
test_annotated_float64()
