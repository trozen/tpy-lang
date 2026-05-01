# Module-level globals don't participate in `literal_default_vars` (which
# is per-function state in FunctionTrackingState), so a global initialized
# from an integer literal does NOT retro-widen at a typed-slot use. This
# is the documented gap covered by the TODO.md "polymorphic integer
# literals" entry; the test pins the current bare type-mismatch error so a
# future C-style fix can update the snapshot in a single place.
from tpy import UInt64


CONST = 5


def f(x: UInt64) -> None:
    pass


def main() -> None:
    f(CONST)   # tpyc: error(/Type mismatch in argument 'x': expected UInt64, got Int32/)


main()
