# Test JsonReader pull-parser: objects, arrays, nesting, escapes, skip, all value types.
from tpy import error_return
from tplib.json import JsonError, JsonReader, JsonToken

@error_return(JsonError)
def test_basic_object() -> None:
    reader = JsonReader('{"name": "Alice", "age": 30, "active": true}')
    reader.read_object_start()
    while reader.has_next():
        key = reader.read_key()
        if key == "name":
            v = reader.read_str()
            print(v)
        elif key == "age":
            v2 = reader.read_int()
            print(v2)
        elif key == "active":
            v3 = reader.read_bool()
            print(v3)
    reader.read_object_end()

@error_return(JsonError)
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
                    v = reader.read_str()
                    print(v)
            reader.read_object_end()
        elif key == "scores":
            reader.read_array_start()
            while reader.has_next():
                v2 = reader.read_int()
                print(v2)
            reader.read_array_end()
    reader.read_object_end()

@error_return(JsonError)
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
            v = reader.read_str()
            print(v)
    reader.read_object_end()

@error_return(JsonError)
def test_float() -> None:
    reader = JsonReader('[3.14, -0.5, 1e3]')
    reader.read_array_start()
    while reader.has_next():
        v = reader.read_float()
        print(v)
    reader.read_array_end()

@error_return(JsonError)
def test_skip() -> None:
    reader = JsonReader('{"keep": 42, "skip": {"nested": [1,2,3]}, "also": "yes"}')
    reader.read_object_start()
    while reader.has_next():
        key = reader.read_key()
        if key == "keep":
            v = reader.read_int()
            print(v)
        elif key == "also":
            v2 = reader.read_str()
            print(v2)
        else:
            reader.skip_value()
    reader.read_object_end()

@error_return(JsonError)
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

@error_return(JsonError)
def test_negative_int() -> None:
    reader = JsonReader('[-42, 0, 100]')
    reader.read_array_start()
    while reader.has_next():
        v = reader.read_int()
        print(v)
    reader.read_array_end()

@error_return(JsonError)
def test_raw_methods() -> None:
    reader = JsonReader('{"name": "Alice", "city": "NYC"}')
    reader.read_object_start()
    while reader.has_next():
        key = reader.read_key_raw()
        print(key)
        v = reader.read_str_raw()
        print(v)
    reader.read_object_end()

def test_describe() -> None:
    data = '{"key" 123}'
    reader = JsonReader(data)
    try:
        reader.read_object_start()
        reader.read_key()
    except JsonError as e:
        print(e.describe(data))
    # Unterminated string
    data2 = '{"name": "hello'
    reader2 = JsonReader(data2)
    try:
        reader2.read_object_start()
        reader2.read_key()
        reader2.read_str()
    except JsonError as e2:
        print(e2.describe(data2))
    # Long context (truncated with ...)
    data3 = '{"first_name": "Alice", "last_name": "Smith", "age" "thirty"}'
    reader3 = JsonReader(data3)
    try:
        reader3.read_object_start()
        while reader3.has_next():
            reader3.read_key()
            reader3.skip_value()
    except JsonError as e3:
        print(e3.describe(data3))
    # Error at end of input (truncated JSON)
    data4 = '{"key": 1'
    reader4 = JsonReader(data4)
    try:
        reader4.read_object_start()
        reader4.read_key()
        reader4.read_int()
        reader4.read_object_end()
    except JsonError as e4:
        print(e4.describe(data4))
    # Malformed numbers: lone minus, minus-dot, minus-exponent
    for bad in ['[-]', '[-.e3]', '[-.]']:
        reader5 = JsonReader(bad)
        try:
            reader5.read_array_start()
            reader5.read_float()
        except JsonError as e5:
            print(e5.describe(bad))
    # No position (bare JsonError)
    err = JsonError()
    print(err.describe("any"))
    # Message only, no position
    err2 = JsonError("custom msg")
    print(err2.describe("any"))

def main() -> None:
    try:
        test_basic_object()
    except JsonError:
        print("ERROR")
    try:
        test_nested()
    except JsonError:
        print("ERROR")
    try:
        test_null_and_escape()
    except JsonError:
        print("ERROR")
    try:
        test_float()
    except JsonError:
        print("ERROR")
    try:
        test_skip()
    except JsonError:
        print("ERROR")
    try:
        test_empty_containers()
    except JsonError:
        print("ERROR")
    try:
        test_negative_int()
    except JsonError:
        print("ERROR")
    try:
        test_raw_methods()
    except JsonError:
        print("ERROR")
    test_describe()

main()

