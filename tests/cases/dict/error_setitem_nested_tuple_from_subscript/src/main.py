# The adjacent source shape for a nested-tuple dict value: reading the element
# out of ANOTHER container. The store itself would be the same bare copy, but the
# nested-tuple element READ has no render yet, so the statement rejects there.
from tpy import Int32


def main() -> None:
    src: dict[Int32, tuple[Int32, tuple[Int32, Int32]]] = {5: (1, (2, 3))}
    d: dict[Int32, tuple[Int32, tuple[Int32, Int32]]] = {}
    d[1] = src[5]  # tpyc: error(/not yet supported.*subscript.elem.tuple/)
    print(len(d))


main()
