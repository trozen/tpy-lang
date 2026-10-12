# `is` against a constructor call: a new object is never identical to
# another, so the test is rejected rather than always False (stricter by
# design: docs/LANGUAGE_FEATURES.md, Operators / Comparison).
class A:
    n: int

    def __init__(self) -> None:
        self.n = 1


def fresh(a: A) -> bool:
    return a is A()  # tpyc: error(/a new object is never identical to another/)


print(fresh(A()))
