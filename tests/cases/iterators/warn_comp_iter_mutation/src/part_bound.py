# The refused `t += v + len([...])` sections with the comprehension bound
# first, so it lowers: each warns as its hand-written nested `for` twin does.
from tpy import int32
from fixtures import grow, grow_rows


def element_loan_bound(xss: list[list[int32]], big: bool) -> int32:  # tpyc: mir(conflict /replacement/)
    t = 0
    for v in xss[0]:
        # the enclosing loop's loan on `xss[0]` survives the comprehension
        # (twin: part_enclosing.for_nest_element)
        n = len([len(w) for w in xss if big and xss[0].pop() > 0])  # tpyc: warning(/Mutation of 'xss\[\.\.\.\]' while iterating/) warning(/this call may move the element the iteration over 'xss\[0\]' still walks/)
        t += v + n
    return t


def unknown_index_bound(xss: list[list[int32]], i: int32, big: bool) -> int32:  # tpyc: mir(conflict /replacement/)
    t = 0
    for v in xss[0]:
        # an index that may be the iterated element
        n = len([len(w) for w in xss if big and xss[i].pop() > 0])  # tpyc: warning(/may hit the element being iterated/) warning(/this call may move the element the iteration over 'xss\[0\]' still walks/)
        t += v + n
    return t


def unknown_index_for(xss: list[list[int32]], i: int32, big: bool) -> int32:  # tpyc: mir(conflict /replacement/)
    t = 0
    for v in xss[0]:
        for w in xss:
            if big:
                xss[i].pop()  # tpyc: warning(/may hit the element being iterated/) warning(/this call may move the element the iteration over 'xss\[0\]' still walks/)
            t += v + len(w)
    return t


def self_named_bound() -> int32:  # tpyc: mir(covered)
    ccs = [[[1], [2, 3]], [[4]]]
    t = 0
    for c in ccs:
        # the target `c` hides the enclosing `c` the source names: growing
        # it grows an element, not the source
        n = sum([len(c) for c in c if grow(c)])  # tpyc: ok
        t += n
    return t * 100 + len(ccs[0][0]) * 10 + len(ccs[1][0])


def self_named_for() -> int32:  # tpyc: mir(covered)
    ccs = [[[1], [2, 3]], [[4]]]
    t = 0
    for c in ccs:
        for d in c:
            if grow(d):  # tpyc: ok
                t += len(d)
    return t * 100 + len(ccs[0][0]) * 10 + len(ccs[1][0])


def other_named_bound(big: bool) -> int32:  # tpyc: mir(conflict /replacement/)
    ccs = [[[1], [2, 3]], [[4]]]
    t = 0
    for c in ccs:
        # a distinct target: growing the source `c` warns
        n = sum([len(x) for x in c if big and grow_rows(c)])  # tpyc: warning(/Passing borrowed container 'c' to non-readonly parameter/) warning(/this call may move the element the iteration over 'c' still walks/)
        t += n
    return t


def other_named_for(big: bool) -> int32:  # tpyc: mir(conflict /replacement/)
    ccs = [[[1], [2, 3]], [[4]]]
    t = 0
    for c in ccs:
        for x in c:
            if big and grow_rows(c):  # tpyc: warning(/Passing borrowed container 'c' to non-readonly parameter/) warning(/this call may move the element the iteration over 'c' still walks/)
                t += len(x)
    return t


def main() -> None:
    print("element_loan_bound:", element_loan_bound([[1, 2], [3]], False))
    print("unknown_index_bound:", unknown_index_bound([[1, 2], [3]], 1, False))
    print("unknown_index_for:", unknown_index_for([[1, 2], [3]], 1, False))
    print("self_named_bound:", self_named_bound())
    print("self_named_for:", self_named_for())
    print("other_named_bound:", other_named_bound(False))
    print("other_named_for:", other_named_for(False))
