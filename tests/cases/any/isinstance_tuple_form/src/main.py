# Tuple-form isinstance on Any: `isinstance(x, (A, B))` matches when the
# stored typeid is *either* A or B. Codegen emits an OR of typeid
# comparisons (not typeid(std::variant<A, B>) which would never match).

from typing import Any


def classify(x: Any) -> str:
    if isinstance(x, (int, str)):
        return "int-or-str"
    if isinstance(x, (float, bool)):
        return "float-or-bool"
    return "other"


def main() -> None:
    print(classify(1))
    print(classify("hi"))
    print(classify(2.5))
    print(classify(None))


main()
