# A walrus binding a VIEW-form value inside a condition: the target needs a
# pre-declaration ahead of the test, which the condition position has no arm
# for, so `y := s` is rejected there.
from tpy import Int32, StrView


def measure(s: StrView) -> Int32:
    if len(y := s) > 0:  # tpyc: error(/expr\.walrus/)
        return len(y)
    return 0


def main() -> None:
    print(measure("abc"))


main()
