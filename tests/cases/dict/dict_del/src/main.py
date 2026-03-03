# del d[key] -- remove key-value pair from dict
from tpy import Int32

def test_basic() -> None:
    d = {"a": Int32(1), "b": Int32(2), "c": Int32(3)}
    print(len(d))
    del d["b"]
    print(len(d))
    print(d)

def test_multi_target() -> None:
    d = {"x": Int32(10), "y": Int32(20), "z": Int32(30)}
    del d["x"], d["z"]
    print(d)
    print(len(d))

def test_del_then_insert() -> None:
    d = {"a": Int32(1), "b": Int32(2)}
    del d["a"]
    d["c"] = Int32(3)
    print(d)

test_basic()
test_multi_target()
test_del_then_insert()
