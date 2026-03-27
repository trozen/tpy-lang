# Test JsonReader pull-parser: objects, arrays, nesting, escapes, skip, all value types.
from tplib.json import JsonReader, JsonToken

def test_basic_object() -> None:
    reader = JsonReader('{"name": "Alice", "age": 30, "active": true}')
    reader.read_object_start()
    while reader.has_next():
        key = reader.read_key()
        if key == "name":
            print(reader.read_str())
        elif key == "age":
            print(reader.read_int())
        elif key == "active":
            print(reader.read_bool())
    reader.read_object_end()

def test_nested() -> None:
    reader = JsonReader('{"user": {"name": "Bob"}, "scores": [1, 2, 3]}')
    reader.read_object_start()
    while reader.has_next():
        key = reader.read_key()
        if key == "user":
            reader.read_object_start()
            while reader.has_next():
                k = reader.read_key()
                if k == "name":
                    print(reader.read_str())
            reader.read_object_end()
        elif key == "scores":
            reader.read_array_start()
            while reader.has_next():
                print(reader.read_int())
            reader.read_array_end()
    reader.read_object_end()

def test_null_and_escape() -> None:
    reader = JsonReader('{"x": null, "msg": "hello\\nworld"}')
    reader.read_object_start()
    while reader.has_next():
        key = reader.read_key()
        if key == "x":
            tok = reader.peek()
            if tok == JsonToken.NONE:
                reader.read_null()
                print("null")
        elif key == "msg":
            print(reader.read_str())
    reader.read_object_end()

def test_float() -> None:
    reader = JsonReader('[3.14, -0.5, 1e3]')
    reader.read_array_start()
    while reader.has_next():
        print(reader.read_float())
    reader.read_array_end()

def test_skip() -> None:
    reader = JsonReader('{"keep": 42, "skip": {"nested": [1,2,3]}, "also": "yes"}')
    reader.read_object_start()
    while reader.has_next():
        key = reader.read_key()
        if key == "keep":
            print(reader.read_int())
        elif key == "also":
            print(reader.read_str())
        else:
            reader.skip_value()
    reader.read_object_end()

def test_empty_containers() -> None:
    reader = JsonReader('{"obj": {}, "arr": []}')
    reader.read_object_start()
    while reader.has_next():
        key = reader.read_key()
        if key == "obj":
            reader.read_object_start()
            reader.read_object_end()
            print("empty_obj")
        elif key == "arr":
            reader.read_array_start()
            reader.read_array_end()
            print("empty_arr")
    reader.read_object_end()

def test_negative_int() -> None:
    reader = JsonReader('[-42, 0, 100]')
    reader.read_array_start()
    while reader.has_next():
        print(reader.read_int())
    reader.read_array_end()

def test_raw_methods() -> None:
    reader = JsonReader('{"name": "Alice", "city": "NYC"}')
    reader.read_object_start()
    while reader.has_next():
        key = reader.read_key_raw()
        print(key)
        print(reader.read_str_raw())
    reader.read_object_end()

test_basic_object()
test_nested()
test_null_and_escape()
test_float()
test_skip()
test_empty_containers()
test_negative_int()
test_raw_methods()
