# A post-sema macro synthesizes a member call carrying no FunctionInfo. The
# arg-free form routes; one WITH an argument has no parameter slots to type the
# argument against, so it rejects.
from nofimod import resolve_bump
from tpy import int32


class Counter:
    n: int32

    def __init__(self) -> None:
        self.n = 0

    def bump(self, k: int32) -> int32:
        self.n += k
        return self.n


def sentinel(c: Counter) -> int32:
    return 0


@resolve_bump
def poke(c: Counter) -> int32:
    return sentinel(c)  # tpyc: error(/expr.method_call/)


def main() -> None:
    c = Counter()
    print(poke(c))


main()
