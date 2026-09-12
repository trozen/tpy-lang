# |= with mismatched dict types produces a specific type error, not "not supported"
from tpy import int32


class Node:
    val: int32

    def __init__(self, val: int32) -> None:
        self.val = val


def main() -> None:
    a: dict[str, Node] = {}
    b: dict[str, int32] = {"x": int32(1)}
    a |= b  # tpyc: error(/Type mismatch.*'|='.*expected Node, got int32/)


main()
