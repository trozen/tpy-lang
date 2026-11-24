# `a += 10` through `V.__add__ -> Own[W]` rebinds `a` to another record type: refused, as
# `a = a + 10` is (LANGUAGE_FEATURES.md, the `__add__` fallback's result check).
from tpy import Own, int32


class W:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class V:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def __add__(self, k: int32) -> Own[W]:
        return W(self.n + k)


def main() -> None:
    a = V(1)
    a += 10  # tpyc: error(/Type mismatch in '\+=' to 'a': expected V, got W/)
    print(a.n)


main()
