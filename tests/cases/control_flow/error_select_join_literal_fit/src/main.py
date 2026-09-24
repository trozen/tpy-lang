# A literal element must fit the sibling's element type: `300` does not fit
# a `list[uint8]`, so the arms share no type (docs/LANGUAGE_FEATURES.md, Conditionals).
from tpy import uint8


def main(c: bool) -> None:
    e: list[uint8] = []
    # `[1, 300]` cannot be pinned to `list[uint8]`.
    y = e if c else [1, 300]  # tpyc: error(/Incompatible types in ternary expression/)
    print(len(y))


main(False)
