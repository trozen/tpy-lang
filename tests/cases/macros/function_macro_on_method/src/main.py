# A @function_macro on record methods: pass 5.5 runs it before the method
# body is type-checked, exposing is_method/self_type so the macro can read
# the enclosing record's field types and annotate body locals. On a
# @staticmethod self_type is None; free functions keep the old behavior.
from methmod import probe
from tpy import int32


class Counter:
    n: int32

    def __init__(self):
        self.n = 0

    @probe
    def bump(self) -> int32:  # tpyc: warning(/method macro on Counter.bump: field n is int32/)
        doubled = self.n + self.n
        self.n = self.n + 1
        return doubled

    @staticmethod
    @probe
    def seed() -> int32:  # tpyc: warning(/macro on seed: no self \(is_method=True\)/)
        return 3


@probe
def free(x: int32) -> int32:  # tpyc: warning(/macro on free: no self \(is_method=False\)/)
    return x + 1


def main() -> None:
    c = Counter()
    c.n = Counter.seed()
    print(c.bump())
    print(c.n)
    print(free(10))


main()
