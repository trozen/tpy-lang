# A union's view member at a result takes nothing but a literal: no lifetime
# check covers a view wrapped in the result
# (BUGS.md#view-return-wrapped-slot-unchecked).
from tpy import StrView, int32


def f(n: int32) -> int32 | StrView:
    s = "value" + str(n)
    # The local's buffer dies with the frame the view would outlive.
    return s  # tpyc: error(/return\.union_view_insert/)


def main() -> None:
    v = f(1)
    if isinstance(v, StrView):
        print(v)


main()
