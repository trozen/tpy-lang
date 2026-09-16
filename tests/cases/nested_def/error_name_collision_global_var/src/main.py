# A nested def whose name also names a module-level VARIABLE, not a function.
# The rule is the same: the `def` makes the name a local of the whole body, so
# the earlier read cannot reach the global. CPython raises UnboundLocalError
# ("cannot access local variable 'counter'") at the same line.
from tpy import int32

counter: int32 = 5


def main() -> None:
    # the read cannot reach the module variable: `counter` is main's local here
    print("before:", counter)  # tpyc: error(/is read before the nested function 'counter'/)

    def counter(x: int32) -> int32:
        return x + 1

    print("after:", counter(1))


main()
