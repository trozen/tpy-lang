# `is None` on an operand that is never None is rejected (stricter by
# design: docs/LANGUAGE_FEATURES.md, Operators / Comparison).
class A:
    n: int

    def __init__(self) -> None:
        self.n = 1


def missing(a: A) -> bool:
    return a is None  # tpyc: error(/'a' is a 'A', which is never None; 'is None' needs an Optional or Ptr operand/)


print(missing(A()))
