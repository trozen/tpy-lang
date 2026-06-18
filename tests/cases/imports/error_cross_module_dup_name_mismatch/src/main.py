# Two records named `Tag` from different modules are distinct types: a
# blue.Tag must NOT coerce into a red.Tag parameter just because the short
# name matches. (The diagnostic renders both as `Tag`; the message itself
# does not yet qualify the colliding short names.)
from red import Tag
from blue import Tag as BlueTag


def take_red(t: Tag) -> int:
    return t.n


def main() -> None:
    take_red(BlueTag(5))  # tpyc: error(/Type mismatch in argument 't'/)


main()
