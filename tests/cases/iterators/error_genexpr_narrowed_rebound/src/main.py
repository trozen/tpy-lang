# A generator expression's body runs at each PULL. A captured name narrowed by
# `isinstance` where the genexpr is written may be rebound by the loop the
# genexpr feeds, and a union has no checked read to fall back on, so it is
# rejected (docs/LANGUAGE_FEATURES.md, "Narrowing of a captured name").
from tpy import int32


def scale(xs: list[int32], k: int32 | str) -> None:
    if isinstance(k, int32):
        # the loop body rebinds `k` between two pulls of the genexpr.
        for v in (x * k for x in xs):  # tpyc: error(/is narrowed here, but the loop this generator expression feeds rebinds it/)
            print(v)
            k = "done"


scale([1, 2, 3], 2)
