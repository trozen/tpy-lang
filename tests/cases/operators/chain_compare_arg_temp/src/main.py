# A chained comparison stops at the first false pair, so a later comparator
# whose argument needs a hoisted temp must not be evaluated. Covers both
# chained-compare renders: the inline && chain (all intermediates duplicable)
# and the statement-expression chain (an intermediate needs binding).
from tpy import Int32


class Counter:
    n: Int32

    def __init__(self) -> None:
        self.n = 0


class Probe:
    tag: Int32

    def __init__(self, c: Counter, tag: Int32) -> None:
        # Counted mutation through a shared reference: proves whether this
        # comparator was evaluated at all.
        c.n += 1
        self.tag = tag


def take(p: Probe | None) -> Int32:
    """Reference param, so a `Probe(...)` rvalue argument needs a hoisted temp."""
    return 1 if p is not None else 0


def inline_chain(c: Counter, a: Int32, b: Int32) -> bool:
    # `b` is duplicable, so this renders as an inline && chain. When a < b is
    # false the third comparator must not be built.
    return a < b < take(Probe(c, 1))


def stmtexpr_chain(c: Counter, a: Int32, xs: list[Int32]) -> bool:
    # xs[0] is a non-duplicable intermediate, forcing the statement-expression
    # render. The last comparator is still skipped when the first pair fails.
    return a < xs[0] < take(Probe(c, 2))


def stmtexpr_chain_long(c: Counter, a: Int32, xs: list[Int32]) -> bool:
    # Two bound intermediates: the middle comparator is itself conditional.
    return a < xs[0] < xs[1] < take(Probe(c, 3))


def first_pair_only(c: Counter, a: Int32, b: Int32) -> bool:
    """Inverse: when every earlier pair passes, the last comparator DOES run."""
    return a < b < take(Probe(c, 4))


def main() -> None:
    # Each check uses a fresh Counter so `n` is exactly the number of Probes
    # actually constructed by that expression.
    c = Counter()
    print("inline_skipped", inline_chain(c, 5, 1), c.n)        # False 0
    c = Counter()
    print("inline_taken", first_pair_only(c, 0, 5), c.n)       # True 1

    c = Counter()
    print("stmtexpr_skipped", stmtexpr_chain(c, 5, [1]), c.n)  # False 0
    c = Counter()
    print("stmtexpr_taken", stmtexpr_chain(c, 0, [5]), c.n)    # True 1

    c = Counter()
    print("long_skip_first", stmtexpr_chain_long(c, 5, [1, 9]), c.n)   # False 0
    c = Counter()
    print("long_skip_mid", stmtexpr_chain_long(c, 0, [5, 1]), c.n)     # False 0
    c = Counter()
    print("long_taken", stmtexpr_chain_long(c, 0, [1, 5]), c.n)        # True 1


main()
