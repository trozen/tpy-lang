# Loop positions: a loop-body local under an escaping nested def, an outer
# reference local the loop reseats or rebinds, a while loop's local and a
# tuple-unpacking loop head.
from typing import Callable
from tpy import int32
from fixtures import P


# block_def: the same loop-body local captured by an escaping nested def
def block_def(ps: list[P]) -> None:
    fs: list[Callable[[], int32]] = []
    for q in ps:
        snap = q

        def get() -> int32:  # tpyc: warning(/nested function 'get' copies the object 'snap'/) warning(/'snap' is bound again on each iteration; the nested function 'get'/)
            return snap.n
        fs.append(get)
    ps[0].n = 8
    print("block_def:", fs[0](), fs[1]())


# reseat / rebind_after: an outer reference local the loop points at another
# object, before or after the lambda: each lambda keeps its own pointer
def reseat(ps: list[P]) -> None:
    fs: list[Callable[[], int32]] = []
    p = ps[0]
    for i in range(len(ps)):
        p = ps[i]
        fs.append(lambda: p.n)  # tpyc: warning(/may keep its own copy of 'p'/) warning(/'p' may be bound again/)
    print("reseat:", fs[0](), fs[1](), fs[2]())


def rebind_after() -> None:
    fs: list[Callable[[], int32]] = []
    p = P(0)
    for i in range(3):
        fs.append(lambda: p.n)  # tpyc: warning(/may keep its own copy of 'p'/) warning(/'p' may be bound again/)
        if i == 1:
            p = P(i + 10)
    print("rebind_after:", fs[0](), fs[1](), fs[2]())


# while_unpack: a while loop's local and a tuple-unpacking loop head
def while_unpack(pairs: list[tuple[int32, int32]]) -> None:
    fs: list[Callable[[], int32]] = []
    i = 0
    while i < 2:
        k = i * 10
        fs.append(lambda: k)  # tpyc: warning(/'k' is bound again/)
        i += 1
    for a, b in pairs:
        fs.append(lambda: a + b)  # tpyc: warning(/'a' is bound again/) warning(/'b' is bound again/)
    print("while_unpack:", fs[0](), fs[1](), fs[2](), fs[3]())


def main() -> None:
    block_def([P(1), P(2)])
    reseat([P(1), P(2), P(3)])
    rebind_after()
    while_unpack([(1, 2), (3, 4)])
