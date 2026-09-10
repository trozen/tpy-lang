# Regime B preserves lambda-body return-TPR inference even in a
# multi-overload group when only ONE candidate is Fn-bearing-by-
# supplied for this call. The lambda's return type comes from the
# body, not the (unresolvable) hint return TPR -- exactly the shape
# that builtin `map[T,U](Fn[[T], U], Iterable[T])` relies on.
from tpy import Fn, Int32, dispatch


# Two overloads at different arities: the 2-arg `apply` is Fn-bearing
# (callable + iterable); the 3-arg overload is non-Fn-bearing for the
# 2-arg call's regime predicate. With 1 Fn-bearing-by-supplied
# candidate, we land in Regime B which uses _infer_arg_types ->
# _analyze_lambda_with_fn_hint -- preserving body-return-TPR
# inference for U.
@dispatch
def apply[T, U](f: Fn[[T], U], xs: list[T]) -> Int32:  # tpyc: ok
    return Int32(0)


@dispatch
def apply[T](xs: list[T], a: T, b: T) -> T:  # tpyc: ok
    return a


def main() -> None:
    xs: list[Int32] = [1, 2, 3]
    # 2-arg call: only the Fn-bearing overload is viable. U comes from
    # the lambda body (`x + Int32(1)` -> Int32). Regime B handles this.
    print(apply(lambda x: x + Int32(1), xs))   # 0 (overload returns 0)


main()
