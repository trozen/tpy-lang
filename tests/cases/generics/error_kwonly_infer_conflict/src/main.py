# A keyword-only argument is inference evidence: one that disagrees with a
# positional argument rejects, as the positional twin `pos(2.0, 3)` does
# (docs/LANGUAGE_FEATURES.md, Generic Functions, "Conflicting evidence").
from tpy import int64


def kw_infer[T](x: T, *, k: T = 1) -> T:
    return k


def main() -> None:
    i: int64 = 3
    # `x` says float, `k` says int64: no `T` is inferred.
    print(kw_infer(2.0, k=i))  # tpyc: error(/Cannot infer type arguments/)


main()
