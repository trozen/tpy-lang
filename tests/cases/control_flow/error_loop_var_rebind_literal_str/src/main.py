# A loop rebinding a `Literal` str local with plain `str` elements is a type
# mismatch (LANGUAGE_FEATURES.md, Functions: Definition).
from typing import Literal


def main() -> None:
    s: Literal["ok"] = "ok"
    xs = ["ok"]
    for s in xs:  # tpyc: error(/for-loop rebinds existing variable 's' of type 'Literal\["ok"\]' with elements of type 'str'/)
        pass
    xs[0] = "no"
    print(s)


main()
