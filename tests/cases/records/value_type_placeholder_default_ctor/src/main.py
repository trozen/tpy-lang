# A slot declared before its first value is built by the type's C++ default
# constructor. A ValueType `__init__` callable without arguments IS that
# constructor, so it runs once more than in CPython (the documented contract,
# LANGUAGE_FEATURES "Placeholders run the default constructor").
from tpy import int32, ValueType


class Noisy(ValueType):
    n: int32

    def __init__(self, n: int32 = 0) -> None:
        print("noisy init", n)
        self.n = n


# A required parameter: the placeholder takes the field-wise default
# constructor, which runs no `__init__`.
class Plain(ValueType):
    n: int32

    def __init__(self, n: int32) -> None:
        print("plain init", n)
        self.n = n


def pick(c: bool) -> None:
    # if hoist: the placeholder runs `Noisy()` before the arm's own call
    if c:
        z = Noisy(1)  # tpyc: ok
    else:
        z = Noisy(2)
    print("noisy", z.n)
    # if hoist of a required-parameter ValueType: no extra `__init__`
    if c:
        p = Plain(3)  # tpyc: ok
    else:
        p = Plain(4)
    print("plain", p.n)


def main() -> None:
    pick(True)


main()
