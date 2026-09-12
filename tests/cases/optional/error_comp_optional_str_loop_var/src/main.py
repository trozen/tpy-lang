# A comprehension whose loop variable is an `Optional[str]` element: only the
# value-repr Optional[cheap scalar] family binds a loop var, so this rejects.
from tpy import int32


def count(parts: list[str | None]) -> int32:
    xs = [p for p in parts]  # tpyc: error(/expr.list_comp/)
    return len(xs)


def main() -> None:
    print(count([None]))


main()
