# An owning call argument inside a CONDITIONALLY EVALUATED operand: the
# argument temp is created where the operand runs, not ahead of the guard.
# Every section pops from `xs` in the always-evaluated part, so what the
# callee reports names the moment the copy was taken -- the pre-fix render
# hoisted it before the pop and reported one element too many.
# The Own[T] slot COPIES the borrowed list on purpose (the pinned copy
# warning); the sections observe evaluation ORDER, so the callees only read.
from tpy import Int32, Own


class Bag:
    vals: list[Int32]

    def __init__(self, vals: list[Int32]) -> None:
        # The print makes a constructor run in a SKIPPED branch visible.
        print("bag", len(vals))
        self.vals = vals


def seen(tag: str, o: Own[list[Int32]]) -> bool:
    print(tag, len(o))
    return True


def seen_n(tag: str, o: Own[list[Int32]]) -> Int32:
    print(tag, len(o))
    return len(o)


def seen_rec(tag: str, b: Own[Bag]) -> bool:
    print(tag, len(b.vals))
    return True


def seen_str(tag: str, s: Own[str]) -> bool:
    print(tag, s)
    return True


def seen_bag(tag: str, b: Bag) -> bool:
    print(tag, len(b.vals))
    return True


def or_rhs(xs: list[Int32]) -> bool:
    # The `or` RHS runs only when the pop yielded 0, so the copy is taken
    # after the pop.
    return (xs.pop() > 0) or seen("or", xs)  # tpyc: warning(/copies list\[Int32\] into owned storage/)


def and_rhs(xs: list[Int32]) -> bool:
    # The `and` RHS: the same region at the other logical operator.
    return (xs.pop() > 0) and seen("and", xs)  # tpyc: warning(/copies list\[Int32\] into owned storage/)


def nested(xs: list[Int32], flag: bool) -> bool:
    # A region inside a region: the inner `and` RHS banks into the outer
    # `or` RHS region.
    return flag or (xs.pop() > 0 and seen("nested", xs))  # tpyc: warning(/copies list\[Int32\] into owned storage/)


def ternary(xs: list[Int32]) -> bool:
    # A ternary ARM: each arm opens its own region.
    return seen("ternary", xs) if xs.pop() > 0 else False  # tpyc: warning(/copies list\[Int32\] into owned storage/)


def chained(xs: list[Int32]) -> bool:
    # Comparators past the first are conditional operands too.
    return 0 < xs.pop() < seen_n("chained", xs)  # tpyc: warning(/copies list\[Int32\] into owned storage/)


def value_select(xs: list[Int32]) -> Int32:
    # The VALUE-select RHS: `a or b` over two Int32 values renders as a
    # ternary over the left, and its right operand is the same region.
    n = xs.pop()
    v: Int32 = n or seen_n("valuesel", xs)  # tpyc: warning(/copies list\[Int32\] into owned storage/)
    return v


def record_payload(xs: list[Int32]) -> bool:
    # `or` RHS with a RECORD payload rather than a container: the owning-slot
    # copy of a whole record defers the same way.
    b = Bag(xs)
    return (b.vals.pop() > 0) or seen_rec("record", b)  # tpyc: warning(/copies Bag into owned storage/)


def own_str_name(xs: list[Int32], tail: str) -> bool:
    # An OWNED str NAME source: the view->owned conversion temp is audited
    # like the container rows, so it defers instead of being refused.
    s = tail + "!"
    return (xs.pop() > 0) or seen_str("ownstr", s)


def ref_param_rvalue(flag: bool) -> bool:
    # A record RVALUE at a plain REFERENCE parameter: a different temp row
    # from the owning-slot copies above, and the constructor's print shows
    # whether it ran in the skipped branch.
    return flag or seen_bag("refparam", Bag([1, 2]))


def left_operand(xs: list[Int32]) -> bool:
    # THE INVERSE: the LEFT operand always evaluates, so its temp stays at
    # the enclosing statement -- no region, no optional slot.
    return seen("left", xs) or xs.pop() > 0  # tpyc: warning(/copies list\[Int32\] into owned storage/)


def main() -> None:
    # Each result binds before it is printed: a callee that prints while the
    # print statement is mid-stream would interleave under TPy but not under
    # CPython, which is a different divergence than the one under test.
    a = or_rhs([1, 2, 0])
    print("or", a)
    b = and_rhs([1, 2, 3])
    print("and", b)
    c = nested([1, 2, 3], False)
    print("nested", c)
    d = ternary([1, 2, 3])
    print("ternary", d)
    e = chained([1, 2, 3])
    print("chained", e)
    f = value_select([1, 2, 0])
    print("valuesel", f)
    g = record_payload([1, 2, 0])
    print("record", g)
    h = own_str_name([1, 2, 0], "kept")
    print("ownstr", h)
    j = ref_param_rvalue(True)
    print("refparam_skipped", j)
    k = ref_param_rvalue(False)
    print("refparam_taken", k)
    i = left_operand([1, 2, 3])
    print("left", i)


main()
