# A callback through a callable value is an opaque, potentially-mutating
# callee: passing a container that has an element borrow to a non-readonly
# callable param warns exactly like the direct call (previously the callable
# path was silently readonly). Covers the Fn (template) param, the Callable
# (std::function) param, and a Callable-typed local -- all three shapes.
#
# Note: each `p = xs[0]` borrow's last read is BEFORE the call, so the
# mutation is in fact safe here -- the warning is conservative (the
# borrow-conflict check is not last-use aware: it fires while any element
# borrow is registered in scope, live across the call or not; see the
# borrow-checker precision TODO). The test asserts the diagnostic fires on
# every callable shape, not that a live-across-call UAF is caught; the read
# is kept before the call so runtime output stays deterministic and matches
# CPython (a genuine cross-call UAF would read reallocated storage).
from typing import Callable
from tpy import Fn, Int32


class P:
    v: Int32

    def __init__(self, v: Int32) -> None:
        self.v = v


def mutate(xs: list[P]) -> None:
    xs.append(P(99))


def use_fn(f: Fn[[list[P]], None]) -> None:
    xs: list[P] = [P(1), P(2)]
    p = xs[0]
    print(p.v)
    f(xs)  # tpyc: warning(/Passing borrowed container/)
    print(len(xs))


def use_callable(f: Callable[[list[P]], None]) -> None:
    xs: list[P] = [P(1), P(2)]
    p = xs[0]
    print(p.v)
    f(xs)  # tpyc: warning(/Passing borrowed container/)
    print(len(xs))


def use_local() -> None:
    f: Callable[[list[P]], None] = mutate
    xs: list[P] = [P(1), P(2)]
    p = xs[0]
    print(p.v)
    f(xs)  # tpyc: warning(/Passing borrowed container/)
    print(len(xs))


def use_direct() -> None:
    xs: list[P] = [P(1), P(2)]
    p = xs[0]
    print(p.v)
    mutate(xs)  # tpyc: warning(/Passing borrowed container/)
    print(len(xs))


def main() -> None:
    use_fn(mutate)
    use_callable(mutate)
    use_local()
    use_direct()


main()
