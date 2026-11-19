# A comprehension inside an enclosing `for`, beside its hand-written nested
# `for` twins: the enclosing loop's loan survives the comprehension.
from tpy import int32
from fixtures import grow_rows


def in_for_body(xs: list[int32], zs: list[int32], big: bool) -> int32:  # tpyc: mir(conflict /replacement/)
    t = 0
    for v in xs:
        # a comprehension inside a for body: the loop's own loan outlives it
        t += len([w for w in zs])
        if big:
            xs.append(v)  # tpyc: warning(/Mutation of 'xs' while iterating/) warning(/may move the elements of 'xs'. but the iteration over 'xs' still walks them/)
    return t


def enclosing_element_loan(xss: list[list[int32]], big: bool) -> int32:  # tpyc: mir(uncovered /order-sensitive eager operands/)
    t = 0
    for v in xss[0]:
        # the enclosing loop's loan on `xss[0]` survives the comprehension
        # over `xss`
        t += v + len([len(w) for w in xss if big and xss[0].pop() > 0])  # tpyc: warning(/Mutation of 'xss\[\.\.\.\]' while iterating/)
    return t


def enclosing_sibling(xss: list[list[int32]], big: bool) -> int32:  # tpyc: mir(uncovered /order-sensitive eager operands/)
    t = 0
    for v in xss[0]:
        # a SIBLING element of the enclosing loop's source may shrink
        t += v + len([len(w) for w in xss if big and xss[1].pop() > 0])  # tpyc: ok
    return t


def enclosing_unknown_index(xss: list[list[int32]], i: int32, big: bool) -> int32:  # tpyc: mir(uncovered /order-sensitive eager operands/)
    t = 0
    for v in xss[0]:
        # an index that may be the iterated element
        t += v + len([len(w) for w in xss if big and xss[i].pop() > 0])  # tpyc: warning(/may hit the element being iterated/)
    return t


def for_nest_element(xss: list[list[int32]], big: bool) -> int32:
    t = 0
    for v in xss[0]:
        # a nested `for` over the whole list keeps the outer element loan
        for w in xss:
            if big:
                xss[0].pop()  # tpyc: warning(/Mutation of 'xss\[\.\.\.\]' while iterating/) warning(/this call may move the element the iteration over 'xss\[0\]' still walks/)
            t += v + len(w)
    return t


def for_nest_sibling(xss: list[list[int32]], big: bool) -> int32:
    t = 0
    for v in xss[0]:
        for w in xss:
            # a sibling element may still shrink; MIR's "may" (BUGS.md#mir-distinct-constant-index-false-may)
            if big:
                xss[1].pop()  # tpyc: warning(/this call may move the element the iteration over 'xss\[0\]' still walks/)
            t += v + len(w)
    return t


def rows(xss: list[list[int32]], big: bool) -> int32:  # tpyc: mir(conflict /replacement/)
    # two loops in one comprehension: growing the outer source moves both walks
    return len([w for v in xss for w in v if big and grow_rows(xss)])  # tpyc: warning(/Passing borrowed container 'xss' to non-readonly parameter/) warning(/may move the elements of 'xss', and the iteration over 'xss' may still walk them/) warning(/may move the elements of 'xss', but the iteration over 'v' still walks them/)


def rows_for(xss: list[list[int32]], big: bool) -> int32:  # tpyc: mir(conflict /replacement/)
    n = 0
    for v in xss:
        for w in v:
            if big and grow_rows(xss):  # tpyc: warning(/Passing borrowed container 'xss' to non-readonly parameter/) warning(/may move the elements of 'xss', and the iteration over 'xss' may still walk them/) warning(/may move the elements of 'xss', but the iteration over 'v' still walks them/)
                n += 1
    return n


def main() -> None:
    print("in_for_body:", in_for_body([1, 2], [3], False))
    print("enclosing_element_loan:", enclosing_element_loan([[1, 2], [3]], False))
    print("enclosing_sibling:", enclosing_sibling([[1, 2], [3]], False))
    print("enclosing_unknown_index:", enclosing_unknown_index([[1, 2], [3]], 1, False))
    print("for_nest_element:", for_nest_element([[1, 2], [3]], False))
    print("for_nest_sibling:", for_nest_sibling([[1, 2], [3]], False))
    print("rows:", rows([[1, 2], [3]], False))
    print("rows_for:", rows_for([[1, 2], [3]], False))
