# The adjacent shape the type-param scalar type-ctor arm must NOT claim: an
# UNBOUNDED type param. Sema picks one overload for the whole template, and
# `int32(x)` resolves to the `[T: AnyFixedInt]` __init__ whose template is the
# fixed-int cast -- at the `int` (BigInt) and `str` instantiations below the
# monomorphic spelling picks `to_fixed_check` and `from_str_check` instead, so
# the bare relay would expand a conversion that does not exist. The reject is
# at the generic body, which is why one case covers both instantiations.
from tpy import int32


def conv[T](x: T) -> int32:
    return int32(x)  # tpyc: error(/call\.type_ctor\.scalar_arg/)


def main() -> None:
    n = 300000000000  # tpyc: warning(/outside default int32 range/)
    print(conv(n), conv("12"))


main()
