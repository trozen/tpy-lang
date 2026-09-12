# The owned-str span element's sibling: a VIEW-typed element keeps rejecting,
# because a view read carries the static-storage literal pin the owned form
# does not.
from tpy import int32, StrView


def main() -> None:
    src: list[StrView] = []
    src.append("alpha")
    src.append("beta")
    tail = src[1:]
    print(len(tail), tail[0])  # tpyc: error(/subscript.elem.strview/)


main()
