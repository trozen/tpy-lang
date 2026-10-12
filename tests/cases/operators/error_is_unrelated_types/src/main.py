# `is` between Optionals of two unrelated classes is rejected (stricter by
# design: docs/LANGUAGE_FEATURES.md, Operators / Comparison); with both
# operands nullable the diagnostic spells the both-None test.
class A:
    n: int

    def __init__(self) -> None:
        self.n = 1


class B:
    n: int

    def __init__(self) -> None:
        self.n = 1


def same(a: A | None, b: B | None) -> bool:
    return a is b  # tpyc: error(/'A' and 'B' are unrelated \(neither is a subclass of the other\); to test that both are None, write 'a is None and b is None'/)


print(same(A(), B()))
