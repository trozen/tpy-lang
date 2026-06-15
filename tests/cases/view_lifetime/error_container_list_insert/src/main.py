# Sibling of error_container_view_temp for list.insert (the rvalue lands in arg
# index 1): storing a temporary into a view-typed list element is rejected.
from tpy import StrView


def make() -> str:
    return "this is long enough to dodge the small-string buffer"


def via_insert() -> None:
    xs: list[StrView] = []
    xs.insert(0, make())  # tpyc: error(/temporary in 'insert'/)
    print(len(xs))


def main() -> None:
    via_insert()


main()
