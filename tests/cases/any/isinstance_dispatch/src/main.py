# Type-dispatch over heterogeneous values: print each value with its
# type-specific representation. Avoids f-strings on the narrowed BigInt
# (BigInt has no std::formatter specialization in TPy's runtime, an
# orthogonal limitation).

from typing import Any


def show(v: Any) -> None:
    if isinstance(v, int):
        print("int", v)
    elif isinstance(v, str):
        print("str", v)
    elif isinstance(v, float):
        print("float", v)
    else:
        print("other")


def main() -> None:
    show(1)
    show("hi")
    show(2.5)
    show(None)


main()
