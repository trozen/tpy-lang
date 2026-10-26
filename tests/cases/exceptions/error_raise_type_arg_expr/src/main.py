# A type argument is a static type (LANGUAGE_FEATURES.md "Generic Functions",
# Error Handling): `raise Outer.E[choose()](7)` is refused, not the call dropped.
from tpy import Own, int32


class Outer:
    class E[T](Exception):
        def __init__(self, code: Own[T]) -> None:
            super().__init__("raised")
            self.code = code


def choose() -> int32:
    return 3


def main() -> None:
    raise Outer.E[choose()](7)  # tpyc: error(/type argument .choose\(\). is not a type; type arguments must be static types/)


main()
