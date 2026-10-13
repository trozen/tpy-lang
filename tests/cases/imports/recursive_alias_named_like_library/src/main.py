# A program's recursive alias named like json's `JsonValue` stays out of json's
# C++, whether the entry module or another program module defines it.
import json
import shapes

type JsonValue = int | list[JsonValue]


def depth(v: JsonValue) -> int:
    if isinstance(v, int):
        return 0
    return 1


def main() -> None:
    # entry module: scalar only, no recursion, because of BUGS.md#program-alias-shares-library-alias-name
    v: JsonValue = 4  # tpyc: ok
    print("main:", depth(v), json.dumps([1, 2]))
    # non-entry module: shapes' alias beside json's
    print("module:", shapes.total_of_nested(), json.dumps([3]))


main()
