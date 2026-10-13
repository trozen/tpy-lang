# A container NAME read in a comprehension element is never its last use, even
# when the source is dead after the comprehension: the element runs once per
# item. So it copies into every slot, and the "copies ... into owned storage"
# warning names the divergence (CPython aliases one list into every slot).
from tpy import int32, Own


def mk() -> Own[list[int32]]:
    return [1, 2]


def main() -> None:
    xs = mk()
    # The subject: a warned copy per slot, never a move.
    ls: list[list[int32]] = [xs for i in range(2)]  # tpyc: warning(/copies list\[int32\] into owned storage/)
    print(len(ls), len(ls[0]), len(ls[1]))


main()
