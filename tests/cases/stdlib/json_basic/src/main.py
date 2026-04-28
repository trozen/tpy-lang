# json.loads / json.dumps round-trip for every JsonValue alternative.
# Output is byte-compared against CPython's json module via the cpy phase.
import json
from json import JSONDecodeError


def roundtrip(s: str) -> str:
    v = json.loads(s)
    return json.dumps(v)


def main() -> None:
    try:
        # Primitives + null
        print(roundtrip("null"))
        print(roundtrip("true"))
        print(roundtrip("false"))
        print(roundtrip("42"))
        print(roundtrip("-7"))
        print(roundtrip("0"))
        print(roundtrip("3.14"))
        print(roundtrip('"hello"'))

        # Object and array
        print(roundtrip('{"name": "Alice", "age": 30}'))
        print(roundtrip("[1, 2, 3]"))

        # Mixed nesting
        print(roundtrip('{"users": [{"id": 1, "name": "Alice"}, {"id": 2, "name": "Bob"}], "count": 2}'))

        # Round-trip preserves int vs float distinction
        print(roundtrip("[1, 2, 3]"))
        print(roundtrip("[1.0, 2.0, 3.0]"))

        # null inside containers
        print(roundtrip('[1, null, "x"]'))
        print(roundtrip('{"a": null}'))

        # String escapes
        print(roundtrip('"line1\\nline2"'))
        print(roundtrip('"quote\\""'))

        # Empty containers
        print(roundtrip("{}"))
        print(roundtrip("[]"))

        # sort_keys
        sk = json.loads('{"b": 1, "a": 2, "c": 3}')
        print(json.dumps(sk, sort_keys=True))
    except JSONDecodeError as e:
        print("ERR:", e.msg)


main()
