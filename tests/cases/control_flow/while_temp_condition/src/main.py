# Temp-producing while conditions (union variant, container literal, record
# ctor rvalue) re-evaluate per iteration; guarded loops fail cleanly on regress.
from tpy import Int32, Float64


# Discriminates on Float64 (== float under CPython) so an int arg narrows
# identically on both runtimes.
def take_vu(v: Int32 | Float64) -> Int32:
    if isinstance(v, Float64):
        return -1
    return v


def eat(xs: list[int]) -> int:
    n = len(xs)
    if n > 0:
        xs.pop()
    return n


def head(xs: list[int]) -> int:
    return xs[0]


def countdown(total: Int32) -> Int32:
    # Variant temp reads loop-mutated state: stale snapshot never terminates.
    it = 0
    while take_vu(total) > 0:
        total -= 1
        it += 1
        if it > 50:
            return -1
    return it


def fresh_literal() -> int:
    # A fresh [1, 2, 3] every iteration keeps eat() returning 3: the guard
    # trips. A single mutated snapshot would drain and exit early (it == 2).
    it = 0
    while eat([1, 2, 3]) > 1:
        it += 1
        if it > 20:
            return it
    return it


def literal_reads_loop_var(start: int) -> int:
    # The literal's element reads a loop-mutated var; termination depends on
    # per-iteration rebuild.
    n = start
    it = 0
    while head([n]) > 0:
        n -= 1
        it += 1
        if it > 50:
            return -1
    return it


def else_break_continue(start: int, stop_at: int) -> int:
    # Restructured head must compose with the loop-else/break/continue label
    # machinery: normal exit runs else, break skips it, continue re-evaluates
    # the temps and condition.
    n = start
    steps = 0
    while head([n]) > 0:
        n -= 1
        if n == stop_at:
            break
        if n == 2:
            continue
        steps += 1
    else:
        return steps + 100
    return steps


class Pack:
    n: Int32

    def __init__(self, n: Int32):
        self.n = n


def weigh(p: Pack) -> Int32:
    return p.n


def ctor_rvalue_cond(start: Int32) -> Int32:
    # Record-ctor rvalue arg temp reads a loop-mutated var each iteration.
    n = start
    it = 0
    while weigh(Pack(n)) > 0:
        n -= 1
        it += 1
        if it > 50:
            return -1
    return it


def nested_loops() -> int:
    # Temp-condition while nested in a for: per-instance checkpoint/flush.
    hits = 0
    for _ in range(2):
        n = 2
        while head([n]) > 0:
            n -= 1
            hits += 1
    return hits


def walrus_cond(stop: Int32) -> Int32:
    # Named walrus pre-decl stays before the loop (visible after it);
    # inverse guard for the named/anonymous flush split.
    src = [3, 2, 1, 0]
    i = 0
    total = 0
    while (n := src[i]) > stop:
        total += n
        i += 1
    return total + n


def main():
    print(countdown(5))
    print(fresh_literal())
    print(literal_reads_loop_var(4))
    print(else_break_continue(5, 3))
    print(else_break_continue(5, -1))
    print(ctor_rvalue_cond(3))
    print(nested_loops())
    print(walrus_cond(0))


main()
