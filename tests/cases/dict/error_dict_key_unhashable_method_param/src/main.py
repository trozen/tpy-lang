# Regression: dict[NonHashable, V] as a *method param* type is rejected.
# The early-registration validate skips the hashable check (sibling records
# aren't fully registered yet); the second-pass walker must also re-validate
# method param + return types, not just fields.
from tpy import Int32


class Point:
    x: Int32

    def __init__(self, v: Int32) -> None:
        self.x = v


class Holder:
    def use(self, d: dict[Point, Int32]) -> None:  # tpyc: error(/Point.*cannot be used as a dict key.*missing __hash__/)
        print(len(d))


def main() -> None:
    h = Holder()
    print(h)


main()
