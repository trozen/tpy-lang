# A comprehension nested inside a conditional operand, whose element needs a
# hoisted arg temp. The temp's decl is flushed into the comprehension's loop
# body -- a scope that is itself inside the conditional -- so the deferral
# cannot bank an emplace against it and degrades to an eager
# `std::optional<T> __tmp = init;` there. That degrade is still correct: the
# loop body only runs when the branch is taken, and the slot still derefs as
# `(*__tmp)`. This pins the fallback so the interaction stays intentional.
from tpy import int32


class Counter:
    n: int32

    def __init__(self) -> None:
        self.n = 0


class Probe:
    tag: int32

    def __init__(self, c: Counter, tag: int32) -> None:
        # Counted mutation through a shared reference: proves whether this
        # element was constructed at all.
        c.n += 1
        self.tag = tag


def take(p: Probe | None) -> int32:
    """Reference param, so a `Probe(...)` rvalue argument needs a hoisted temp."""
    return 1 if p is not None else 0


def comp_in_ternary(c: Counter, cond: bool, xs: list[int32]) -> int32:
    # The comprehension sits in the `then` arm; its per-element temp must not
    # run when `cond` is false.
    ys = [take(Probe(c, i)) for i in xs] if cond else [0]
    return len(ys)


def comp_in_and(c: Counter, cond: bool, xs: list[int32]) -> bool:
    # Same shape one level down, in an `and` RHS.
    return cond and len([take(Probe(c, i)) for i in xs]) > 0


def main() -> None:
    # A fresh Counter per check, so `n` is exactly the number of Probes built.
    c = Counter()
    print("ternary_skipped", comp_in_ternary(c, False, [1, 2]), c.n)  # 1 0
    c = Counter()
    print("ternary_taken", comp_in_ternary(c, True, [1, 2]), c.n)     # 2 2
    c = Counter()
    print("and_skipped", comp_in_and(c, False, [1, 2]), c.n)          # False 0
    c = Counter()
    print("and_taken", comp_in_and(c, True, [1, 2]), c.n)             # True 2


main()
