# Regression: user record with `__hash__` but no `__eq__` is rejected --
# the gate requires both Hashable and Equatable conformance.
from tpy import Int32, UInt64


class HashOnly:
    val: Int32

    def __init__(self, v: Int32) -> None:
        self.val = v

    def __hash__(self) -> UInt64:
        return UInt64(self.val)


def main() -> None:
    s: set[HashOnly] = set()  # tpyc: error(/HashOnly.*cannot be used as a set element.*missing __eq__/)
    s.add(HashOnly(1))
    print(len(s))


main()
