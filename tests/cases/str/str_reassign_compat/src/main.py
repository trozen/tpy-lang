# Regression guard: the reassignment compat check added for str-view locals
# must NOT reject compatible reassigns -- literal->literal and owned->view
# widening through the same PendingViewType branch.
def make_str() -> str:
    return "owned"

def main() -> None:
    s = "asd"
    s = "qwe"
    print(s)           # tpyc: ok
    s = s + "x"        # owned source: must promote the local to owned, not stay a view
    print(s)
    s = make_str()
    print(s)

main()
