# Returning a blue.Tag where red.Tag is declared: the mismatch message
# qualifies the colliding short names in the return-value context too.
from red import Tag
from blue import Tag as BlueTag


def make_red() -> Tag:
    return BlueTag(5)  # tpyc: error(/expected red.Tag, got blue.Tag/)


def main() -> None:
    make_red()


main()
