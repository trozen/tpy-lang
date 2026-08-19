# A conditionally-evaluated operand whose argument needs a hoisted temp must
# not run when the branch is skipped: the temp materializes at the operand,
# not at the enclosing statement. Covers and/or RHS, both ternary arms, and
# nested chains.
from tpy import Int32


class Counter:
    n: Int32

    def __init__(self) -> None:
        self.n = 0


class Probe:
    tag: Int32

    def __init__(self, c: Counter, tag: Int32) -> None:
        # Counted mutation through a shared reference: proves whether this
        # operand was evaluated at all.
        c.n += 1
        self.tag = tag


def take(p: Probe | None) -> bool:
    """Reference param, so a `Probe(...)` rvalue argument needs a hoisted temp."""
    return p is not None


def and_skips_rhs(c: Counter) -> bool:
    return False and take(Probe(c, 1))  # LHS false -> Probe must NOT be built


def and_runs_rhs(c: Counter) -> bool:
    return True and take(Probe(c, 2))  # LHS true -> Probe IS built


def or_skips_rhs(c: Counter) -> bool:
    return True or take(Probe(c, 3))  # LHS true -> Probe must NOT be built


def or_runs_rhs(c: Counter) -> bool:
    return False or take(Probe(c, 4))  # LHS false -> Probe IS built


def ternary_skips_else(c: Counter) -> bool:
    return True if True else take(Probe(c, 5))  # else arm must NOT be built


def ternary_skips_then(c: Counter, cond: bool) -> bool:
    return take(Probe(c, 6)) if cond else False  # then arm skipped when cond false


def nested_and(c: Counter, inner: bool) -> bool:
    # Regions nest: the temp defers to the INNER short circuit, so a false
    # `inner` skips it even though the outer `and` was taken.
    return True and (inner and take(Probe(c, 7)))


def nested_mixed(c: Counter, first: bool, deep: bool) -> bool:
    # Probe 8 is the always-evaluated LHS of the `or`. Probe 9 sits one level
    # deeper: reached only when the `or` LHS is false, and built only when
    # `deep` is true. `first` exists to make that LHS falsifiable -- `take`
    # alone is always True, so without it the `or` would short-circuit and
    # Probe 9 would be dead in both directions. A single-level region would
    # bank Probe 9's emplace into the OUTER prefix and build it regardless.
    return True and ((take(Probe(c, 8)) and first)
                     or (deep and take(Probe(c, 9))))


def nested_ternary(c: Counter, inner: bool) -> bool:
    return (take(Probe(c, 10)) if inner else False) if True else False


def unconditional(c: Counter) -> bool:
    """Inverse: a temp in an unconditional position must still materialize."""
    return take(Probe(c, 11))


def main() -> None:
    # Each check uses a fresh Counter so `n` is exactly the number of Probes
    # actually constructed by that expression.
    c = Counter()
    print("and_skips_rhs", and_skips_rhs(c), c.n)      # False 0
    c = Counter()
    print("and_runs_rhs", and_runs_rhs(c), c.n)        # True 1
    c = Counter()
    print("or_skips_rhs", or_skips_rhs(c), c.n)        # True 0
    c = Counter()
    print("or_runs_rhs", or_runs_rhs(c), c.n)          # True 1
    c = Counter()
    print("ternary_skips_else", ternary_skips_else(c), c.n)   # True 0
    c = Counter()
    print("ternary_then_skipped", ternary_skips_then(c, False), c.n)  # False 0
    c = Counter()
    print("ternary_then_taken", ternary_skips_then(c, True), c.n)     # True 1
    c = Counter()
    print("nested_and_skipped", nested_and(c, False), c.n)    # False 0
    c = Counter()
    print("nested_and_taken", nested_and(c, True), c.n)       # True 1
    c = Counter()
    print("nested_mixed_or_short", nested_mixed(c, True, True), c.n)   # True 1
    c = Counter()
    print("nested_mixed_shallow", nested_mixed(c, False, False), c.n)  # False 1
    c = Counter()
    print("nested_mixed_deep", nested_mixed(c, False, True), c.n)      # True 2
    c = Counter()
    print("nested_ternary_skipped", nested_ternary(c, False), c.n)  # False 0
    c = Counter()
    print("nested_ternary_taken", nested_ternary(c, True), c.n)     # True 1
    c = Counter()
    print("unconditional", unconditional(c), c.n)      # True 1


main()
