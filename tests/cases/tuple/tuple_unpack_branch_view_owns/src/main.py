# str tuple targets assigned in BOTH if/else branches, each from a DISTINCT
# branch-local source (t1 / t2 -- assigned in only one branch, so neither is
# hoisted; each dies at its branch's end). The targets a/b ARE hoisted (assigned
# on all paths, read after the if), so they outlive their source -- a view would
# dangle, so they must own (std::string). Distinct source names are essential: a
# shared `t` would hoist the source too, making a view safe and the owning moot.
def make(tag: str) -> tuple[str, str]:
    return (tag + "-alpha-long-enough-to-heap-allocate",
            tag + "-beta-long-enough-to-heap-allocate")


def main() -> None:
    flag = len("ab") > 1
    if flag:
        t1 = make("X")
        a, b = t1
    else:
        t2 = make("Y")
        a, b = t2
    print(a)
    print(b)


main()
