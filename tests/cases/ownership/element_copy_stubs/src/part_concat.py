# The spelled `+` remedy, `copy(xs) + copy(zs)`, over unannotated lists at a
# local, a return and an annotated declaration; and `copy(xs) + zs`, which
# still copies `zs`, silently (BUGS.md#list-copy-concat-elements-copy-unwarned).
from tpy import int32, copy, Own


class E:
    def __init__(self, v: int32) -> None:
        self.v = v


def joined() -> Own[list[E]]:
    xs = [E(1), E(2)]
    zs = [E(3)]
    # return: both copies spelled
    return copy(xs) + copy(zs)  # tpyc: ok


def main() -> None:
    xs = [E(1), E(2)]
    zs = [E(3)]
    # local: both copies spelled
    ys = copy(xs) + copy(zs)  # tpyc: ok
    ys[0].v = 7
    # only the left copy spelled
    ws = copy(xs) + zs  # tpyc: ok
    # annotated declaration
    ts: list[E] = copy(xs) + copy(zs)  # tpyc: ok
    ts[2].v = 8
    print("concat:", xs[0].v, zs[0].v, len(ws), len(joined()))
