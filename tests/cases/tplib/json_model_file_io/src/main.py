# Test @model file I/O: save_json, load_json, try_load_json.
from tpy import Int32
from tplib.json.model import model
from tplib.json.parser import JsonError

@model
class Item:
    name: str
    count: Int32
    active: bool

def test_roundtrip() -> None:
    item = Item("widget", 42, True)
    item.save_json("/tmp/_tpy_test_json_io.json")
    loaded = Item.load_json("/tmp/_tpy_test_json_io.json")
    print(loaded.name)
    print(loaded.count)
    print(loaded.active)
    print(item == loaded)

def test_pretty() -> None:
    item = Item("gadget", 7, False)
    item.save_json("/tmp/_tpy_test_json_io2.json", indent=2)
    loaded = Item.load_json("/tmp/_tpy_test_json_io2.json")
    print(loaded.name)
    print(item == loaded)

def test_try_load() -> None:
    Item("ok", 1, True).save_json("/tmp/_tpy_test_json_io3.json")
    try:
        c = Item.try_load_json("/tmp/_tpy_test_json_io3.json")
        print(c.name)
    except JsonError as e:
        print("error: " + e.message)

def test_try_load_bad() -> None:
    with open("/tmp/_tpy_test_json_io_bad.json", "w") as f:
        f.write("{bad json}")
    try:
        c = Item.try_load_json("/tmp/_tpy_test_json_io_bad.json")
        print(c.name)
    except JsonError as e:
        print("caught: " + e.message)

test_roundtrip()
test_pretty()
test_try_load()
test_try_load_bad()
