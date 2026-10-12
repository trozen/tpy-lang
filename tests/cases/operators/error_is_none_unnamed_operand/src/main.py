# `is None` on a call result that is never None is rejected (stricter by
# design: docs/LANGUAGE_FEATURES.md, Operators / Comparison); an operand with
# no simple spelling is named "the operand".
class A:
    n: int

    def __init__(self, n: int) -> None:
        self.n = n


G = A(1)


def glob() -> A:
    return G


def main() -> None:
    print(glob() is None)  # tpyc: error(/the operand is a 'A', which is never None; 'is None' needs an Optional or Ptr operand/)


main()
