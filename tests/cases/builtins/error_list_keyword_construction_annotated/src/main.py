# An annotated list() construction binds its keyword as an unannotated one does
# (refused: the iterable is positional-only), not as an argument-less list().
from tpy import int32


def main() -> None:
    xs = [1, 2]
    ys: list[int32] = list(iterable=xs)  # tpyc: error(/No matching overload for 'list' with argument types \(iterable=/)
    print(ys)


main()
