# A typed container confirms or widens an unannotated list literal's element,
# never narrows it (docs/LANGUAGE_FEATURES.md "List Literal Inference").
from tpy import uint8


def take8(v: list[uint8]) -> None:
    v.append(7)


def main() -> None:
    ys = [200]
    # The list holds int32 values; a uint8 element would change every use.
    take8(ys)  # tpyc: error(/'ys' holds int32 elements, and it is passed here as list\[uint8\]; annotate its first binding: ys: list\[uint8\] = \[\.\.\.\]/)
    print(ys)


main()
