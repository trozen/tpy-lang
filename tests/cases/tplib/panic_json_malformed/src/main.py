# Test panic on malformed JSON (unterminated string).
from tplib.json import JsonReader

def main() -> None:
    reader = JsonReader('{"name": "hello')
    reader.read_object_start()
    reader.has_next()
    reader.read_key()
    reader.read_str()

main()
