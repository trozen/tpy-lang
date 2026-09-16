# The other direction of return_view_at_owned_opt: an OWNED local at a
# VIEW-inner Optional return (`-> StrView | None`). A copy cannot fix this one
# -- the view would point into the local's buffer, which dies at the return --
# so it keeps rejecting.
from tpy import int32, StrView


def b(n: int32) -> StrView | None:
    s = "x" * n
    return s  # tpyc: error(/not yet supported/)


def main() -> None:
    r = b(3)
    print(-1 if r is None else len(r))


main()
