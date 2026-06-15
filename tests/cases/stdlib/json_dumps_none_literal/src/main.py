# A None element in a list/dict literal coerced into the recursive-union
# JsonValue serializes as null (list, dict value, nested; no JsonValue import).
import json


def main() -> None:
    print(json.dumps([1, None, 3]))
    print(json.dumps({"a": None, "b": 2}))
    print(json.dumps({"a": [1, None], "b": None}))
    print(json.dumps([None, [None, [1, None]]]))


main()
