# Empty dict {} and dict() infer key/value types from subsequent subscript assignment
from tpy import int32, int64

def test_literal() -> None:
    d = {}  # tpyc: type(dict[str, int32])
    d["hello"] = 42
    print(d)

def test_dict_ctor() -> None:
    d = dict()  # tpyc: type(dict[int32, str])
    d[1] = "a"
    d[2] = "b"
    print(d)

def test_multiple() -> None:
    d = {}  # tpyc: type(dict[str, int32])
    d["x"] = 10
    d["y"] = 20
    d["z"] = 30
    print(d)

def test_numeric_widen() -> None:
    d = {}  # tpyc: type(dict[str, int64])
    d["a"] = int32(1)
    d["b"] = int64(2)
    print(d)

def test_getitem_after_infer() -> None:
    d = {}  # tpyc: type(dict[str, int32])
    d["x"] = 10
    v: int32 = d["x"]
    print(v)

def takes_dict(d: dict[str, int32]) -> None:
    for k in d:
        print(k, d[k])

def test_param_context() -> None:
    d = {}  # tpyc: type(dict[str, int32])
    d["a"] = 1
    takes_dict(d)

def test_param_only() -> None:
    d = dict()  # tpyc: type(dict[str, int32])
    takes_dict(d)

test_literal()
test_dict_ctor()
test_multiple()
test_numeric_widen()
test_getitem_after_infer()
test_param_context()
test_param_only()
