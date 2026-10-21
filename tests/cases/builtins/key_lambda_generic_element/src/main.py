# key=lambda over a container whose element type involves the enclosing type
# param (tuple[T, ...]), inside generic sorted/min/max calls.
from tpy import int32, Own


def ranked[T](pairs: list[tuple[T, int32]]) -> Own[list[tuple[T, int32]]]:
    return sorted(pairs, key=lambda p: -p[1])


def by_name[T](pairs: list[tuple[T, str]]) -> Own[list[tuple[T, str]]]:
    # Key returns a non-int Comparable (str): K must infer from the body, not
    # collapse to its Comparable bound.
    return sorted(pairs, key=lambda p: p[1])


# the owned return copies the winning tuple's T element, a class T that
# CPython would hand back itself: hedged per element, as a scalar T is
def smaller[T](a: tuple[T, int32], b: tuple[T, int32]) -> Own[tuple[T, int32]]:
    return min(a, b, key=lambda p: p[1])  # tpyc: warning(/may copy T into owned storage \(tuple element 0\)/)


def larger[T](a: tuple[T, int32], b: tuple[T, int32]) -> Own[tuple[T, int32]]:
    return max(a, b, key=lambda p: p[1])  # tpyc: warning(/may copy T into owned storage \(tuple element 0\)/)


# local: the fresh min result bound, then rebound to a borrowed parameter;
# the binding holds a copy, hedged whole at the local
# (per element waits on the binding facts: BUGS.md#generic-lending-local-copies-unwarned;
# T = str only: a class T fails the C++ build, BUGS.md#generic-tuple-local-lending-call-no-lift)
def rebound[T](a: tuple[T, int32], b: tuple[T, int32], c: bool) -> int32:
    t = min(a, b, key=lambda p: p[1])  # tpyc: warning(/may copy tuple\[T, int32\] into local 't'/)
    if c:
        t = a
    return t[1]


def main() -> None:
    ps: list[tuple[str, int32]] = [("a", 3), ("b", 1), ("c", 2)]
    for k, n in ranked(ps):
        print(k, n)
    ns: list[tuple[int32, str]] = [(1, "c"), (2, "a"), (3, "b")]
    for nm, s in by_name(ns):
        print(nm, s)
    sk, sn = smaller(("a", 3), ("b", 1))
    print("min:", sk, sn)
    lk, ln = larger(("a", 3), ("b", 1))
    print("max:", lk, ln)
    print("rebound:", rebound(("a", 3), ("b", 1), True),
          rebound(("a", 3), ("b", 1), False))


main()
