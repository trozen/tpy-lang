# `is` between instances of two unrelated classes is rejected (stricter by
# design: docs/LANGUAGE_FEATURES.md, Operators / Comparison).
class A:
    n: int

    def __init__(self) -> None:
        self.n = 1


class B:
    n: int

    def __init__(self) -> None:
        self.n = 1


def same(a: A, b: B) -> bool:
    return a is b  # tpyc: error(/'A' and 'B' are unrelated \(neither is a subclass of the other\)$/)


print(same(A(), B()))
