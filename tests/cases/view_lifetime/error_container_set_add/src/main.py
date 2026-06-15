# Sibling of error_container_view_temp for set.add: storing a temporary rvalue
# into a view-typed set element is rejected (the temporary dies, leaving a
# dangling view in the set).
from tpy import StrView


def make() -> str:
    return "this is long enough to dodge the small-string buffer"


def via_add() -> None:
    xs: set[StrView] = set()
    xs.add(make())  # tpyc: error(/temporary in 'add'/)
    print(len(xs))


def main() -> None:
    via_add()


main()
