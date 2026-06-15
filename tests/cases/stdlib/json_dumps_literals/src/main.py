# json.dumps on raw list/dict literals WITHOUT importing the internal JsonValue
# alias -- the idiomatic form: the cross-module recursive-union parameter
# resolves at a call site that doesn't import the alias. CPython byte-compatible.
import json


def main() -> None:
    print(json.dumps([1, 2, 3]))
    print(json.dumps({"a": 1, "b": 2}))
    print(json.dumps([1, [2, 3], 4]))
    print(json.dumps({"a": [1, 2], "b": 3}))
    print(json.dumps(["x", True, 1.5]))


main()
