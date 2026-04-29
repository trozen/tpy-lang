# `Any(value)` constructs an Any cell holding `value` -- TPy-specific
# sugar for the INTO_ANY coercion. Useful for inline construction
# without typed intermediates: `[Any(p), Any(q)]`.

from typing import Any


def main() -> None:
    a = Any(213)
    b = Any("hello")
    c = Any(3.14)
    d = Any(None)
    print(a)
    print(b)
    print(c)
    print(d)
    # Inline in a container literal
    items: list[Any] = [Any(1), Any("two"), Any(True)]
    print(len(items))


main()
