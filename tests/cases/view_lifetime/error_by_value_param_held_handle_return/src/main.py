# A handle field read out of a by-value record parameter is the function's
# own copy's: the body may rebind the copy to a record viewing a local, so a
# handle-field exemption would be a silent dangle
# (BUGS.md#view-handle-field-of-own-storage-refused).
from tpy import ValueType, StrView


class V(ValueType):
    def __init__(self, v: StrView) -> None:
        self.v = v


def f(x: V) -> StrView:
    loc = "L" * 40
    x = V(loc)
    return x.v  # tpyc: error(/parameter 'x' \(the function's own copy\)/)


def main() -> None:
    s = "s" * 40
    print(f(V(s)) == "L" * 40)


main()
