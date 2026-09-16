# The adjacent shape the type-param scalar type-ctor arm must NOT claim: an
# UNBOUNDED type param. Sema picks one overload for the whole template, and
# `int32(x)` resolves to the `[T: AnyFixedInt]` __init__ whose template is the
# fixed-int cast -- at the `int` (BigInt) and `str` instantiations below the
# monomorphic spelling picks `to_fixed_check` and `from_str_check` instead, so
# the pick only holds when the caller's own bound says it does. The error is at
# the generic body, which is why one case covers both instantiations.
from tpy import int32


def conv[T](x: T) -> int32:
    return int32(x)  # tpyc: error(/does not satisfy bound 'AnyFixedInt'.*picked once for the whole generic body/)


def main() -> None:
    # BigInt, so the annotation is not something the compiler would infer here
    n: int = 300000000000
    print(conv(n), conv("12"))


main()
