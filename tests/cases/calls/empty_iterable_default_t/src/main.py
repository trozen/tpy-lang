# Empty container literals carry an UnknownElementType placeholder for T
# in generic Iterable[T]/Sequence[T] inference. Two ways for T to land on
# a real type:
#   (1) @type_param_default(T=DefaultInt) -- opt-in fallback when no other
#       arg-side evidence pins T (used here by stdlib sorted/all/any/iter/
#       enumerate/reversed and by the user `tag` helper).
#   (2) A later arg whose type pins T directly -- the empty-list's
#       UnknownElement placeholder is overridden by any concrete type seen
#       on a subsequent matching arg (see `pick_or` below).
from typing import Iterable
from tpy.extern import type_param_default, DefaultInt


# Single-arg generic: only signal is the empty list, so T must default.
@type_param_default(T=DefaultInt)
def tag[T](xs: Iterable[T]) -> str:
    return "ok"


# Multi-arg generic: empty xs gives UnknownElement; `fallback` then pins
# T from the int literal. No @type_param_default needed.
def pick_or[T](xs: Iterable[T], fallback: T) -> T:
    for x in xs:
        return x
    return fallback


# Two iterables of the same T -- exercises both directions of the
# UnknownElementType placeholder rule in match_type_with_inference. Either
# arg position carrying [] must defer to the concrete one.
def pair[T](xs: Iterable[T], ys: Iterable[T]) -> str:
    return "ok"


def main() -> None:
    print(sorted([]))           # tpyc: ok
    print(all([]))              # tpyc: ok
    print(any([]))              # tpyc: ok
    print(tag([]))              # tpyc: ok
    print(pick_or([], 7))       # tpyc: ok
    print(pair([1, 2], []))     # tpyc: ok
    print(pair([], [1, 2]))     # tpyc: ok

    for i, x in enumerate([]):  # tpyc: ok
        print(i, x)
    print("enumerate done")

    for x in iter([]):          # tpyc: ok
        print(x)
    print("iter done")

    # Guards the PendingListType path through
    # `_infer_protocol_type_arg_structurally`: list doesn't declare
    # Sequence in its extends list, so conformance falls into the
    # structural helper -- which used to leak TypeParamRef(T) for
    # non-NominalType args.
    for x in reversed([]):      # tpyc: ok
        print(x)
    print("reversed done")


main()
