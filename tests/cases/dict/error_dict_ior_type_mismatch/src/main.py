# |= with mismatched dict types produces a specific type error, not "not supported"
from tpy import Int32


class Node:
    val: Int32

    def __init__(self, val: Int32) -> None:
        self.val = val


def main() -> None:
    a: dict[str, Node] = {}
    b: dict[str, Int32] = {"x": Int32(1)}
    a |= b  # tpyc: error(/Type mismatch.*'|='.*expected Node, got Int32/)


main()
