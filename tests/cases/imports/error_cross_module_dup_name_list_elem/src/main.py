# The list-literal element-type mismatch message also qualifies a same-short-name
# cross-module collision (a different message shape than `expected X, got Y`).
from red import Tag
from blue import Tag as BlueTag


def main() -> None:
    xs: list[Tag] = [BlueTag(5)]  # tpyc: error(/has type blue.Tag, incompatible with annotated element type red.Tag/)
    print(len(xs))


main()
