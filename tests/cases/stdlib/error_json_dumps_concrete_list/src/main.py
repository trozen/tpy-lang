# A concrete list variable (list[int32]) is intentionally NOT implicitly
# converted into json.dumps's JsonValue param (sibling of the dict case).
import json
from tpy import int32


def main() -> None:
    xs: list[int32] = [1, 2]
    print(json.dumps(xs))  # tpyc: error(/not implicitly converted into the recursive-union/)


main()
