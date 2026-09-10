# Calling an overloaded user generic where the only candidate's T has a
# protocol bound, with an empty container and no @type_param_default --
# previously surfaced the misleading "Type '???' does not satisfy '<bound>'"
# from the bound-violation diagnostic at calls.py:_analyze_builtin_function_overloads.
# Now the bound check skips UnknownElementType-typed inferences, and the
# generic single-call retry surfaces the cleaner "Cannot infer type
# arguments" diagnostic.
from typing import Iterable
from tpy import Comparable, dispatch


@dispatch
def g[T: Comparable](xs: Iterable[T]) -> str:
    return "comp"


@dispatch
def g(xs: Iterable[float], y: float) -> str:
    return "float"


def main() -> None:
    print(g([]))  # tpyc: error(/Cannot infer type arguments for 'g'/)


main()
