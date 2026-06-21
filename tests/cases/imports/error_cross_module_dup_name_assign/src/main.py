# Assigning a blue.Tag to a red.Tag-annotated local: the mismatch message
# qualifies the colliding short names in the assignment context too.
from red import Tag
from blue import Tag as BlueTag


def main() -> None:
    x: Tag = BlueTag(5)  # tpyc: error(/expected red.Tag, got blue.Tag/)
    print(x.n)


main()
