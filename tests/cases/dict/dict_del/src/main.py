# del d[key] -- remove key-value pair from dict
from tpy import int32

def test_basic() -> None:
    d = {"a": int32(1), "b": int32(2), "c": int32(3)}
    print(len(d))
    del d["b"]
    print(len(d))
    print(d)

def test_multi_target() -> None:
    d = {"x": int32(10), "y": int32(20), "z": int32(30)}
    del d["x"], d["z"]
    print(d)
    print(len(d))

def test_del_then_insert() -> None:
    d = {"a": int32(1), "b": int32(2)}
    del d["a"]
    d["c"] = int32(3)
    print(d)

test_basic()
test_multi_target()
test_del_then_insert()
