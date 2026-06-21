# The comprehension element-type mismatch message qualifies a same-short-name
# cross-module collision too (another non-`expected X, got Y` formatter).
from red import Tag
from blue import Tag as BlueTag


def main() -> None:
    blues: list[BlueTag] = [BlueTag(1), BlueTag(2)]
    xs: list[Tag] = [b for b in blues]  # tpyc: error(/has type blue.Tag, incompatible with annotated element type red.Tag/)
    print(len(xs))


main()
