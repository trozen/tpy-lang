# Sources and targets: a temporary receiver, a target named like its source
# or like a parameter, a different container, the loan's end, a range source,
# a writing range bound, a read-only body.
from tpy import int32
from fixtures import grow, grow_len, grow_rows, mk_box


def temp_receiver(big: bool) -> int32:  # tpyc: mir(uncovered /named argument needs complete temporary plan/)
    n = 0
    if big:
        # the source borrows from a temporary receiver: the comprehension's
        # clause source keeps it until the comprehension ends (so this never
        # runs, but would be safe)
        n = len([e * 2 for e in mk_box().items_m()])  # tpyc: ok
    return n


def self_named_source() -> int32:  # tpyc: mir(uncovered /order-sensitive eager operands/)
    ccs = [[[1], [2, 3]], [[4]]]
    t = 0
    for c in ccs:
        # the source `c` is the ENCLOSING binding, which the comprehension
        # cannot reach by name: growing the target `c` grows an element, not
        # the source, so no iteration warning
        t += sum([len(c) for c in c if grow(c)])  # tpyc: ok
    return t * 100 + len(ccs[0][0]) * 10 + len(ccs[1][0])


def self_named_nested(big: bool) -> int32:  # tpyc: mir(uncovered /order-sensitive eager operands/)
    ccs = [[[1], [2, 3]], [[4]]]
    t = 0
    for c in ccs:
        # the write sits in an INNER comprehension: its fact reaches the outer
        # one, so `t + sum(...)` is order-sensitive as in self_named_source
        # (never run: growing `r` while walking it would not end)
        t += sum([len([w for w in r if big and grow(r)]) for r in c])  # tpyc: warning(/Passing borrowed container 'r' to non-readonly parameter/)
    return t * 100 + len(ccs[0][0]) * 10 + len(ccs[1][0])


def other_named_source(big: bool) -> int32:  # tpyc: mir(uncovered /order-sensitive eager operands/)
    ccs = [[[1], [2, 3]], [[4]]]
    t = 0
    for c in ccs:
        # the twin with a distinct target still warns when `c` itself grows
        t += sum([len(x) for x in c if big and grow_rows(c)])  # tpyc: warning(/Passing borrowed container 'c' to non-readonly parameter/)
    return t


def other_container(xs: list[int32], ys: list[int32]) -> int32:  # tpyc: mir(conflict /alias_obligation/)
    # growing a DIFFERENT container is fine
    return len([v for v in xs if grow(ys)])  # tpyc: ok


def other_container_for(xs: list[int32], ys: list[int32]) -> int32:  # tpyc: mir(conflict /alias_obligation/)
    out: list[int32] = []
    for v in xs:
        if grow(ys):  # tpyc: ok
            out.append(v)
    return len(out)


def param_shadow(xs: list[int32], xss: list[list[int32]], big: bool) -> int32:  # tpyc: mir(covered)
    # the target `xs` hides the parameter `xs`: growing it grows a row of
    # `xss`, not the parameter or the source
    return len([len(xs) for xs in xss if big and grow(xs)])  # tpyc: ok


def param_shadow_for(xs: list[int32], xss: list[list[int32]], big: bool) -> int32:  # tpyc: mir(covered)
    # the `for` twin binds another name: a `for` rebinding the parameter
    # refuses (control_flow/error_loop_var_rebind_param)
    out: list[int32] = []
    for ys in xss:
        if big and grow(ys):  # tpyc: ok
            out.append(len(ys))
    return len(out)


def range_bound(xs: list[int32]) -> int32:  # tpyc: mir(uncovered /unsupported range bound/)
    # an inner loop's range bound grows the outer source
    return len([i for v in xs for i in range(grow_len(xs))])  # tpyc: warning(/Passing borrowed container 'xs' to non-readonly parameter/)


def range_bound_for(xs: list[int32]) -> int32:  # tpyc: mir(uncovered /unsupported range bound/)
    out: list[int32] = []
    for v in xs:
        for i in range(grow_len(xs)):  # tpyc: warning(/Passing borrowed container 'xs' to non-readonly parameter/)
            out.append(i)
    return len(out)


def after_comp(xs: list[int32]) -> int32:  # tpyc: mir(covered)
    out = [v for v in xs]
    # the loan ends with the comprehension
    xs.append(len(out))  # tpyc: ok
    grow(xs)  # tpyc: ok
    return len(xs)


def range_source(xs: list[int32]) -> int32:  # tpyc: mir(covered)
    # a range source iterates no container
    return len([i for i in range(3) if grow(xs)])  # tpyc: ok


def read_only(xs: list[int32]) -> int32:  # tpyc: mir(uncovered /unsupported expression/)
    # a comprehension that only reads its source
    return len([v + len(xs) for v in xs if v in xs])  # tpyc: ok


def main() -> None:
    print("temp_receiver:", temp_receiver(False))
    print("self_named_source:", self_named_source())
    print("other_named_source:", other_named_source(False))
    print("other_container:", other_container([1, 2], [0]))
    print("other_container_for:", other_container_for([1, 2], [0]))
    # the growth lands on the rows, never on the parameter `xs`
    xs = [1]
    xss = [[1, 2], [3]]
    print("param_shadow:", param_shadow(xs, xss, True), len(xs), len(xss[0]), len(xss[1]))
    ys = [1]
    yss = [[1, 2], [3]]
    print("param_shadow_for:", param_shadow_for(ys, yss, True), len(ys), len(yss[0]), len(yss[1]))
    print("self_named_nested:", self_named_nested(False))
    # an empty source: the growing bound never runs
    print("range_bound:", range_bound([]))
    print("range_bound_for:", range_bound_for([]))
    print("after_comp:", after_comp([1, 2]))
    print("range_source:", range_source([1]))
    print("read_only:", read_only([1, 2]))
