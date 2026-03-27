# Test JSON escape handling: \b, \f, \uXXXX in reader; control char escaping in writer.
from tpy import Int32, error_return
from tplib.json import JsonError, JsonReader, JsonWriter

@error_return(JsonError)
def test_reader_standard() -> None:
    reader = JsonReader('["a\\nb", "c\\td", "e\\\\f", "g\\"h"]')
    reader.read_array_start()
    while reader.has_next():
        s = reader.read_str()
        print(len(s))
        print(s)
    reader.read_array_end()

@error_return(JsonError)
def test_reader_bf() -> None:
    reader = JsonReader('["x\\by", "x\\fy"]')
    reader.read_array_start()
    while reader.has_next():
        s = reader.read_str()
        print(len(s))
        print(ord(s[1]))
    reader.read_array_end()

@error_return(JsonError)
def test_reader_unicode() -> None:
    reader = JsonReader('["\\u0041", "\\u004F\\u004B"]')
    reader.read_array_start()
    while reader.has_next():
        s = reader.read_str()
        print(s)
    reader.read_array_end()

def test_writer_control_chars() -> None:
    w = JsonWriter()
    w.array_start()
    w.write_str("a\tb")
    w.write_str("c\nd")
    w.write_str(chr(8) + "x")
    w.write_str(chr(12) + "y")
    w.write_str(chr(0) + "z")
    w.array_end()
    print(w.finish())

@error_return(JsonError)
def test_roundtrip() -> None:
    w = JsonWriter()
    w.array_start()
    w.write_str("line1\nline2")
    w.write_str("tab\there")
    w.array_end()
    json = w.finish()
    reader = JsonReader(json)
    reader.read_array_start()
    while reader.has_next():
        s = reader.read_str()
        print(s)
    reader.read_array_end()

def main() -> None:
    try:
        test_reader_standard()
    except JsonError:
        print("ERROR")
    try:
        test_reader_bf()
    except JsonError:
        print("ERROR")
    try:
        test_reader_unicode()
    except JsonError:
        print("ERROR")
    test_writer_control_chars()
    try:
        test_roundtrip()
    except JsonError:
        print("ERROR")

main()
