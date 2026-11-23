# The comprehension as an operand: it writes what its body writes, at every
# position a literal takes, beside the hand-written `for` and call twins.
from tpy import int32, Own
from fixtures import Cell, grow, pick


def grow_and_len(ys: list[int32]) -> int32:
    ys.append(7)
    return len(ys)


def pair(a: int32, b: int32) -> int32:
    return a * 100 + b


def take(ys: Own[list[int32]]) -> Own[list[int32]]:
    return ys


def count(ys: list[int32]) -> int32:
    return len(ys)


def order_comp(xs: list[int32], big: bool) -> int32:  # tpyc: mir(uncovered /order-sensitive eager operands/)
    # a comprehension operand that writes orders like its call twin below
    return pair(len([w for w in xs if big and grow(xs)]), len(xs))  # tpyc: warning(/Passing borrowed container 'xs' to non-readonly parameter/)


def order_read(xs: list[int32]) -> int32:  # tpyc: mir(covered)
    # a comprehension operand that writes nothing orders freely
    return pair(len([w for w in xs]), len(xs))  # tpyc: ok


def order_call(xs: list[int32], big: bool) -> int32:  # tpyc: mir(uncovered /order-sensitive eager operands/)
    if big:
        return pair(grow_and_len(xs), len(xs))
    return pair(len(xs), len(xs))


def via_pick(xs: list[int32], big: bool) -> int32:  # tpyc: mir(conflict /replacement/)
    # the source grows through a borrow-returning call (sema is silent)
    return len([v for v in xs if big and grow(pick(xs))])  # tpyc: warning(/may move the elements of 'pick\(xs\)'. but the iteration over 'xs' still walks them/)


def via_pick_for(xs: list[int32], big: bool) -> int32:  # tpyc: mir(conflict /replacement/)
    n = 0
    for v in xs:
        if big and grow(pick(xs)):  # tpyc: warning(/may move the elements of 'pick\(xs\)'. but the iteration over 'xs' still walks them/)
            n += 1
    return n


def nested_places(xs: list[int32], ys: list[int32], big: bool) -> int32:  # tpyc: mir(conflict /replacement/)
    # an inner comprehension over a place grows the OUTER source
    return len([[w for w in ys if big and grow(xs)] for v in xs])  # tpyc: warning(/Passing borrowed container 'xs' to non-readonly parameter/) warning(/may move the elements of 'xs'. and the iteration over 'xs' may still walk them/)


def own_argument(xs: list[int32], big: bool) -> int32:  # tpyc: mir(conflict /replacement/)
    # handed over to an `Own` parameter
    ys = take([v for v in xs if big and grow(xs)])  # tpyc: warning(/Passing borrowed container 'xs' to non-readonly parameter/) warning(/may move the elements of 'xs'. but the iteration over 'xs' still walks them/)
    return len(ys)


def borrowed_argument(xs: list[int32], big: bool) -> int32:  # tpyc: mir(conflict /replacement/)
    # lent to a borrowed parameter of a user function
    return count([v for v in xs if big and grow(xs)])  # tpyc: warning(/Passing borrowed container 'xs' to non-readonly parameter/) warning(/may move the elements of 'xs'. but the iteration over 'xs' still walks them/)


def ternary_arm(xs: list[int32], big: bool) -> int32:  # tpyc: mir(uncovered /unsupported record initializer/)
    # one arm of a conditional expression
    ys = [v for v in xs if big and grow(xs)] if big else [1]  # tpyc: warning(/Passing borrowed container 'xs' to non-readonly parameter/)
    return len(ys)


def range_shadow() -> int32:  # tpyc: mir(covered)
    i = 5
    # the comprehension's `i` is its own: the enclosing `i` keeps 5
    ys = [i for i in range(3) if i > 0]
    return i * 10 + len(ys)


def record_element(xs: list[int32], big: bool) -> int32:  # tpyc: mir(conflict /replacement/)
    # a record element built per pass
    cs = [Cell(v) for v in xs if big and grow(xs)]  # tpyc: warning(/Passing borrowed container 'xs' to non-readonly parameter/) warning(/may move the elements of 'xs'. but the iteration over 'xs' still walks them/)
    return len(cs)


def bound_sibling(xss: list[list[int32]], big: bool) -> int32:  # tpyc: mir(conflict /replacement/)
    t = 0
    for v in xss[0]:
        # a SIBLING element may shrink, the comprehension bound first; MIR's
        # "may" (BUGS.md#mir-distinct-constant-index-false-may)
        n = len([len(w) for w in xss if big and xss[1].pop() > 0])  # tpyc: warning(/this call may move the element the iteration over 'xss\[0\]' still walks/)
        t += v + n
    return t


def comp_len(xs: list[int32]) -> int32:  # tpyc: mir(covered) mir_summary(known)
    # a body with a comprehension has a known summary, as its `append` twin
    return len([v for v in xs])


def append_len(xs: list[int32]) -> int32:  # tpyc: mir(covered) mir_summary(known)
    out: list[int32] = []
    for v in xs:
        out.append(v)
    return len(out)


def calls_comp(xs: list[int32], big: bool) -> int32:  # tpyc: mir(conflict /replacement/)
    t = 0
    for v in xs:
        # a call of the comprehension body keeps the later warning
        t += comp_len(xs)
        if big:
            grow(pick(xs))  # tpyc: warning(/may move the elements of 'pick\(xs\)'. but the iteration over 'xs' still walks them/)
    return t


def calls_append(xs: list[int32], big: bool) -> int32:  # tpyc: mir(conflict /replacement/)
    t = 0
    for v in xs:
        t += append_len(xs)
        if big:
            grow(pick(xs))  # tpyc: warning(/may move the elements of 'pick\(xs\)'. but the iteration over 'xs' still walks them/)
    return t


def main() -> None:
    print("order_comp:", order_comp([1, 2], False))
    print("order_read:", order_read([1, 2]))
    print("order_call:", order_call([1, 2], False))
    print("via_pick:", via_pick([1, 2], False))
    print("via_pick_for:", via_pick_for([1, 2], False))
    print("nested_places:", nested_places([1, 2], [3], False))
    print("own_argument:", own_argument([1, 2], False))
    print("borrowed_argument:", borrowed_argument([1, 2], False))
    print("ternary_arm:", ternary_arm([1, 2], False))
    print("range_shadow:", range_shadow())
    print("record_element:", record_element([1, 2], False))
    print("bound_sibling:", bound_sibling([[1, 2], [3]], False))
    print("calls_comp:", calls_comp([1, 2], False))
    print("calls_append:", calls_append([1, 2], False))
