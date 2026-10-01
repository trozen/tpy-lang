# A fully owned tuple NAME at its last use passed to a MIXED `tuple[Own[A], B]`
# slot: the owned element should move and the borrowed one point at the
# name's own element, a per-element conversion with no render yet -- a located
# reject (BUGS.md#owned-tuple-last-use-into-mixed-param-rejects), never a C++
# build error. The live pass copies the owned element and warns:
# tuple/mixed_own_param_writes.
from tpy import int32, Own


class Alpha:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class Beta:
    m: int32

    def __init__(self, m: int32) -> None:
        self.m = m


def mixed_sink(p: tuple[Own[Alpha], Beta]) -> int32:
    return p[0].n + p[1].m


def mixed_caller(p: tuple[Own[Alpha], Own[Beta]]) -> int32:
    return mixed_sink(p)  # tpyc: error(/call\.arg_shape\.tuple/)


def main() -> None:
    print(mixed_caller((Alpha(1), Beta(2))))


main()
