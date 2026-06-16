# A user-defined generic function with an Fn-typed param, called from INSIDE an
# enclosing generic function. Guards the two sema layers of the generic-key-
# lambda fix on the broader user-generic path (not just the sorted/min/max
# builtins): the lambda param binds to the caller's type param (calls.py guard),
# and a bounded Fn-return type param infers from the lambda body instead of
# collapsing to its bound (type_ops seed-skip).
from tpy import Fn, Int32, Comparable, Own


# K is in the return, so it seeds from the return hint; the caller's T
# (enclosing-scope) must still be usable to type the lambda param.
def map_keys[T, K: Comparable](xs: list[T], f: Fn[[T], K]) -> Own[list[K]]:
    out: list[K] = []
    i = 0
    while i < len(xs):
        out.append(f(xs[i]))
        i += 1
    return out


# T is in the return (seed non-empty) but bounded K is ONLY in the Fn param, so
# without the seed-skip K collapses to Comparable and body inference breaks.
def above_first[T, K: Comparable](xs: list[T], f: Fn[[T], K]) -> Own[list[T]]:
    out: list[T] = []
    threshold = f(xs[0])
    i = 0
    while i < len(xs):
        if not (f(xs[i]) < threshold):
            out.append(xs[i])  # tpyc: warning(/may copy .* into owned storage/)
        i += 1
    return out


def names[T](pairs: list[tuple[T, str]]) -> Own[list[str]]:
    return map_keys(pairs, lambda p: p[1])  # tpyc: ok


def keep[T](pairs: list[tuple[T, Int32]]) -> Own[list[tuple[T, Int32]]]:
    return above_first(pairs, lambda p: p[1])


def main() -> None:
    ps: list[tuple[Int32, str]] = [(1, "c"), (2, "a"), (3, "b")]
    for s in names(ps):
        print(s)
    qs: list[tuple[str, Int32]] = [("a", 3), ("b", 7), ("c", 1)]
    for k, n in keep(qs):
        print(k, n)


main()
