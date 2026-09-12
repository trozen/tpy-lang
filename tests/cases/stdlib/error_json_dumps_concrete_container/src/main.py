# A concrete container variable (dict[str, int32]) is intentionally NOT
# implicitly converted into json.dumps's JsonValue param (it would be a hidden
# deep copy); the rejection points at the explicit alternatives.
import json


def main() -> None:
    d = {"a": 123}
    print(json.dumps(d))  # tpyc: error(/not implicitly converted into the recursive-union/)


main()
