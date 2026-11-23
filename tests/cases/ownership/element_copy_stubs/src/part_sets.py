# Set algebra copies the elements it keeps: `&` keeps the smaller operand's
# (the right one on a tie), as CPython does. The copies are silent
# (BUGS.md#list-copy-concat-elements-copy-unwarned).
from tpy import int32, uint64, copy


class H:
    def __init__(self, n: int32, tag: str) -> None:
        self.n = n
        self.tag = tag

    def __hash__(self) -> uint64:
        return hash(self.n)

    # Compares fewer fields than H holds: which operand's element a result
    # keeps is observable through `tag`.
    def __eq__(self, o: "H") -> bool:
        return self.n == o.n


def tags(s: set[H]) -> str:
    out: list[str] = []
    for h in s:
        out.append(h.tag)
    return "".join(sorted(out))


def main() -> None:
    big = {H(1, "a"), H(2, "a"), H(5, "a")}
    small = {H(2, "b")}
    both = big & small  # tpyc: ok
    union = big | small  # tpyc: ok
    t = {H(9, "t")}
    t |= small  # tpyc: ok
    print("sets:", len(both), len(union), len(t))
    # The spelled copies keep what CPython keeps.
    print("sets: and", tags(copy(big) & copy(small)))
    print("sets: and tie", tags(copy(small) & {H(2, "c")}))
    cb = copy(big)
    print("sets: intersection", tags(cb.intersection(copy(small))))
    # `&=` keeps the receiver's own elements when it is the smaller operand.
    few = {H(2, "f")}
    few &= big  # tpyc: ok
    print("sets: iand", tags(few))
    few2 = {H(5, "g"), H(7, "g")}
    few2.intersection_update(big)
    print("sets: intersection_update", tags(few2))
