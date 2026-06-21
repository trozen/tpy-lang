# Two records named `Tag` from different modules are distinct types: a
# blue.Tag must NOT coerce into a red.Tag parameter just because the short
# name matches. The diagnostic qualifies the colliding short names with their
# module so the (correct) rejection is legible.
from red import Tag
from blue import Tag as BlueTag


def take_red(t: Tag) -> int:
    return t.n


def main() -> None:
    take_red(BlueTag(5))  # tpyc: error(/expected red.Tag, got blue.Tag/)


main()
