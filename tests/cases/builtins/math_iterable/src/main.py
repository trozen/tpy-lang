# math.prod / fsum / sumprod / dist accept Iterable[float] (not just list[float]).
# Exercises the codegen fix for list-literal-as-protocol + iterator-based
# length-mismatch detection in sumprod/dist.
import math


def main() -> None:
    # List literal passed directly -- previously failed codegen with
    # std::vector<1.0>{...} garbage.
    print(math.prod([2.0, 3.0, 4.0]))
    print(math.fsum([0.1, 0.1, 0.1, 0.1, 0.1]))

    # range() as Iterable[float]. Note: the Iterable<double> C++ concept
    # doesn't actually check element type (it only requires __iter__);
    # int->double coercion happens inside the function body when the iterator
    # yields int32_t and it's assigned to a double. Also: TPy's prod returns
    # float (per signature), while CPython's math.prod is polymorphic and
    # returns int for all-int input. Using fsum (which always returns float)
    # keeps output deterministic across both.
    t: float = math.fsum(range(1, 11))  # 1+2+...+10 = 55.0
    print(t)

    # Bound list variable (sanity -- already worked before the fix)
    xs: list[float] = [1.0, 2.0, 3.0, 4.0]
    print(math.prod(xs))             # 24
    print(math.fsum(xs))             # 10

    # sumprod with two list literals
    print(math.sumprod([1.0, 2.0, 3.0], [4.0, 5.0, 6.0]))  # 32

    # sumprod with mixed sources (list literal + range)
    print(math.sumprod([1.0, 2.0, 3.0, 4.0], range(1, 5)))  # 30

    # dist with list literals
    print(math.dist([0.0, 0.0], [3.0, 4.0]))  # 5.0

    # Empty list literal flows through Iterable[T]: the target's element type
    # is pushed into the pending-list resolver so `[]` concretizes as float.
    # dist returns 0.0 for both-empty (matches CPython). sumprod([], []) is
    # not asserted here because CPython returns int 0 (start=0 default, no
    # multiplication happens), while TPy's Iterable[float]->float signature
    # always returns 0.0; the divergence is on the return type, not the
    # computation.
    print(math.prod([], start=1.0))   # 1.0 (start)
    print(math.fsum([]))              # 0.0
    print(math.dist([], []))          # 0.0


main()
