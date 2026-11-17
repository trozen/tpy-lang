# The function's own copy, `Own[...]`, may be re-pointed by its body, so a
# view read through a Ptr it holds is the function's storage too
# (docs/LANGUAGE_FEATURES.md: a parameter the function holds its own copy of).
from tpy import Ptr, ValueType, StrView, Own


class Inner(ValueType):
    def __init__(self, s: str) -> None:
        self.s = s


def last_name(xs: Own[list[Ptr[Inner]]]) -> StrView:
    zs = [Inner("n" * 40)]
    xs.append(zs[0])
    return xs[len(xs) - 1].s  # tpyc: error(/Cannot return StrView referencing parameter 'xs' \(the function's own copy\)/)


def main() -> None:
    a = Inner("a" * 40)
    xs: list[Ptr[Inner]] = [a]
    print(last_name(xs)[:2])


main()
