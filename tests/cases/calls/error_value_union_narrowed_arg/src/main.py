# A narrowed value-union subject at a value-union parameter: the narrowed
# read still binds the variant, so no argument temp may be hoisted for it.
from tpy import Int32, Float64


def take_vu(v: Int32 | Float64) -> Int32:
    if isinstance(v, Int32):
        return v
    return 0


def f(v: Int32 | Float64) -> Int32:
    if isinstance(v, Int32):
        # `v` is narrowed here but its binding is still the variant.
        return take_vu(v)  # tpyc: error(/expr\.call:call\.arg_shape\.union/)
    return 0


def main() -> None:
    print(f(1))


main()
