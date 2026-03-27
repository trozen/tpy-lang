# Test error on malformed JSON (unterminated string).
from tpy import error_return
from tplib.json import JsonError, JsonReader

@error_return(JsonError)
def parse_bad() -> None:
    reader = JsonReader('{"name": "hello')
    reader.read_object_start()
    reader.has_next()
    reader.read_key()
    reader.read_str()

def main() -> None:
    try:
        parse_bad()
    except JsonError:
        print("caught: malformed json")

main()
