# Empty dict {} and dict() infer key/value types from subsequent subscript assignment
from tpy import Int32, Int64

def test_literal() -> None:
    d = {}  # tpyc: type(dict[str, Int32])
    d["hello"] = 42
    print(d)

def test_dict_ctor() -> None:
    d = dict()  # tpyc: type(dict[Int32, str])
    d[1] = "a"
    d[2] = "b"
    print(d)

def test_multiple() -> None:
    d = {}  # tpyc: type(dict[str, Int32])
    d["x"] = 10
    d["y"] = 20
    d["z"] = 30
    print(d)

def test_numeric_widen() -> None:
    d = {}  # tpyc: type(dict[str, Int64])
    d["a"] = Int32(1)
    d["b"] = Int64(2)
    print(d)

def test_getitem_after_infer() -> None:
    d = {}  # tpyc: type(dict[str, Int32])
    d["x"] = 10
    v: Int32 = d["x"]
    print(v)

def takes_dict(d: dict[str, Int32]) -> None:
    for k in d:
        print(k, d[k])

def test_param_context() -> None:
    d = {}  # tpyc: type(dict[str, Int32])
    d["a"] = 1
    takes_dict(d)

def test_param_only() -> None:
    d = dict()  # tpyc: type(dict[str, Int32])
    takes_dict(d)

test_literal()
test_dict_ctor()
test_multiple()
test_numeric_widen()
test_getitem_after_infer()
test_param_context()
test_param_only()
