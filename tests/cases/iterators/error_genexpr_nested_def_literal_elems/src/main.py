# A genexpr in a nested def over the ENCLOSING function's literal of literals:
# the loop var's container type settles only when the enclosing function ends,
# after the nested def's frame needed it. An annotated container works.
from tpy import int32


def main() -> None:
    grid = [[1], [2, 3]]

    def inner() -> int32:
        return sum(len(r) for r in grid)  # tpyc: error(/cannot iterate a container whose elements are container literals/)

    print(inner())


main()
