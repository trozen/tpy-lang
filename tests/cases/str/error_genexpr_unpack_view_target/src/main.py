# A generator expression unpacking a tuple whose first target is a VIEW: the
# unpack binding has no arm for the view element, so it is rejected today.
from tpy import int32, StrView


def take(vs: list[tuple[StrView, int32]]) -> int32:
    return sum(len(k) + v for k, v in vs)  # tpyc: error(/genexpr\.unpack/)


def main() -> None:
    vs: list[tuple[StrView, int32]] = []
    vs.append((StrView("p"), 1))
    print(take(vs))


main()
