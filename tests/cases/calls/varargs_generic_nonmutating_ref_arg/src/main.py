# Generic vararg with T substituted to a reference type at the call site.
# After substitution elem_type is the concrete Box (not TypeParamRef), so the
# marking gate fires -- guards the post-substitution path (the gate excludes
# unbounded TypeParamRef, but the resolved param is concrete here). Uses
# explicit [Box] to sidestep a pre-existing inference bug where T gets bound
# to `Box&` for ref-typed args (unrelated to this fix; see TODO).
from tpy import Int32, nocopy


@nocopy
class Box:
    val: Int32

    def __init__(self, v: Int32) -> None:
        self.val = v


def count[T](*items: T) -> Int32:
    return len(items)


def via_param(b: Box, c: Box) -> Int32:
    return count[Box](b, c)  # tpyc: ok


def main() -> None:
    x = Box(3)
    y = Box(4)
    print(via_param(x, y))


main()
