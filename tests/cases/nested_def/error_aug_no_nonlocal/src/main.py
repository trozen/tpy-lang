# An inner def's `x += 1` without `nonlocal` makes `x` the inner def's own
# local, read before any binding: CPython's UnboundLocalError, never the
# enclosing `x`.
from tpy import int32

def main() -> None:
    x: int32 = 0
    def f() -> None:
        x += 1  # tpyc: error(/'x' is modified before it is bound on line 9: .* does not see the local 'x' of 'main'/)
    f()

main()
