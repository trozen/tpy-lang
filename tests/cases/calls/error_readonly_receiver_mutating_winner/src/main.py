# On a readonly receiver the arguments still pick the variant: a mutating
# variant fitting them better is refused, not replaced by a worse-fitting
# readonly one (docs/OVERLOAD_DESIGN.md "The receiver kind"; the readonly
# call rule, docs/LANGUAGE_FEATURES.md "Overloading").
from tpy import dispatch, int32, int64, readonly


class M:
    n: int32

    def __init__(self) -> None:
        self.n = 0

    @dispatch
    def m(self, x: int32) -> str:
        self.n += 1
        return "int32"

    @dispatch
    @readonly
    def m(self, x: int64) -> str:
        return "int64"


def ro(p: readonly[M], k: int32) -> str:
    return p.m(k)  # tpyc: error(/Cannot call non-readonly method 'm' on readonly reference/)


def main() -> None:
    a = M()
    print(ro(a, 1))


main()
