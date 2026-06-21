# bytes sibling of tuple_unpack_branch_view_owns: bytes tuple targets assigned
# in BOTH if/else branches, each from a DISTINCT branch-local source (t1 / t2,
# neither hoisted). The hoisted targets a/b outlive their branch-local source,
# so they must own (tpy::bytes / std::vector), not alias it -- a view would
# dangle. A shared `t` name would hoist the source and make a view safe.
def make(tag: bytes) -> tuple[bytes, bytes]:
    return (tag + b"-alpha-long-enough-to-heap", tag + b"-beta-long-enough-to-heap")


def main() -> None:
    flag = len("ab") > 1
    if flag:
        t1 = make(b"X")
        a, b = t1
    else:
        t2 = make(b"Y")
        a, b = t2
    print(len(a))
    print(len(b))


main()
