# json.dumps pretty-print with indent. CPython byte-compatible.
import json
from json import JSONDecodeError


def main() -> None:
    try:
        flat = json.loads('{"name": "Alice", "age": 30}')
        print(json.dumps(flat, indent=2))
        print("---")
        print(json.dumps(flat, indent=4))

        # Nested
        nested = json.loads('{"users": [{"id": 1, "name": "Alice"}, {"id": 2, "name": "Bob"}], "count": 2}')
        print("---")
        print(json.dumps(nested, indent=2))

        # Empty containers
        empty_obj = json.loads("{}")
        print("---")
        print(json.dumps(empty_obj, indent=2))
        empty_arr = json.loads("[]")
        print("---")
        print(json.dumps(empty_arr, indent=2))

        # Combined indent + sort_keys
        print("---")
        sk = json.loads('{"b": 1, "a": 2, "c": 3}')
        print(json.dumps(sk, indent=2, sort_keys=True))
    except JSONDecodeError as e:
        print("ERR:", e.msg)


main()
