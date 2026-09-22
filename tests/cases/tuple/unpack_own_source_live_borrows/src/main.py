# Unpacking an Own-element tuple that stays LIVE after the unpack borrows the
# elements instead of consuming them, like the scalar `x = t` off an owning
# local: a write through the target reaches the source (CPython aliases), and
# the same unpack at the source's LAST use still moves the element out. At
# module scope the pointer-slot target aims straight into the tuple global.
from tpy import Own, int32, nocopy


class Box:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


@nocopy
class NBox:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def mk() -> tuple[Own[Box], int32]:
    return (Box(1), 1)


def nmk() -> tuple[Own[NBox], int32]:
    return (NBox(1), 1)


def sink(x: Own[Box]) -> Own[Box]:
    return x


def nsink(x: Own[NBox]) -> Own[NBox]:
    return x


# free function: the source is read after the unpack, so `x` borrows t[0].
def live_local() -> None:
    t = mk()
    x, k = t  # tpyc: ok
    x.n = 9
    print("live_local", t[0].n, k)


# free function, last use: the element moves out; nothing is copied.
def last_use_moves() -> None:
    t = mk()
    x, k = t  # tpyc: ok
    print("last_use_moves", sink(x).n + k)


# @nocopy element, live source: a borrow, so no copy is asked for and no
# use-after-move is raised (the scalar @nocopy twin binds `x = t` the same).
def nocopy_live() -> None:
    t = nmk()
    x, k = t  # tpyc: ok
    x.n = 9
    print("nocopy_live", t[0].n, k)


# @nocopy element, last use: consumed by move.
def nocopy_last_use() -> None:
    t = nmk()
    x, k = t  # tpyc: ok
    print("nocopy_last_use", nsink(x).n + k)


# two unpacks of one source: both borrow, both see the write.
def two_unpacks() -> None:
    t = mk()
    x, k = t  # tpyc: ok
    y, j = t
    x.n = 9
    print("two_unpacks", y.n, t[0].n, k + j)


# owned tuple PARAM (`std::tuple<Box, int32_t>&&`), read after the unpack:
# the same borrow, so the write reaches the param's element -- and, as for a
# scalar Own param that is only ever borrowed, the ownership goes unused.
def param_live(p: tuple[Own[Box], int32]) -> int32:  # tpyc: warning(/never consumed/)
    x, k = p  # tpyc: ok
    x.n = 9
    return p[0].n + k


# @nocopy owned tuple param read after its unpack: a borrow, no copy asked for
# (and no unconsumed warning: a @nocopy drop is a legitimate consume).
def nocopy_param_live(p: tuple[Own[NBox], int32]) -> int32:  # tpyc: ok
    x, k = p  # tpyc: ok
    x.n = 9
    return p[0].n + k


# a borrowed target fed to an Own sink: the scalar's warned copy, not a move.
# The copy is the acknowledged (warned) Own-copy divergence, pinned by the
# warning; the output prints only what TPy and CPython agree on.
def sink_off_live() -> None:
    t = mk()
    x, k = t
    y = sink(x)  # tpyc: warning(/copies Box into owned storage/)
    y.n = 9
    print("sink_off_live", y.n, k, t[1])


# owned tuple param, last use: consumed.
def param_consumed(p: tuple[Own[Box], int32]) -> int32:
    x, k = p  # tpyc: ok
    return sink(x).n + k


def mk_own() -> Own[tuple[int32, NBox]]:
    return (1, NBox(1))


def relay() -> Own[tuple[int32, NBox]]:
    return mk_own()


# fresh rvalue source through a RELAY call: the holder owns the result and
# the @nocopy element binds into it, the same as off the direct call.
def relayed_call() -> None:
    n, b = relay()  # tpyc: ok
    b.n = 9
    print("relayed_call", n, b.n)


class H:
    def __init__(self) -> None:
        pass

    # method body: the same verdict as the free function.
    def live_in_method(self) -> None:
        t = mk()
        x, k = t  # tpyc: ok
        x.n = 9
        print("live_in_method", t[0].n, k)


# module-level statement: the pointer-slot global points into the tuple global.
tg = mk()
gx, gk = tg  # tpyc: ok
gx.n = 9

# a tuple-of-references GLOBAL (a tuple of pointer slots) unpacked in a body.
V = Box(5)
pair_g: tuple[Box, int32] = (V, 1)


def unpack_borrow_global() -> None:
    a, b = pair_g  # tpyc: ok
    a.n = 9
    print("unpack_borrow_global", V.n, b)


def main() -> None:
    print("module_level", tg[0].n, gk)
    unpack_borrow_global()
    live_local()
    last_use_moves()
    nocopy_live()
    nocopy_last_use()
    two_unpacks()
    print("param_live", param_live((Box(1), 1)))
    print("param_consumed", param_consumed((Box(1), 1)))
    print("nocopy_param_live", nocopy_param_live((NBox(1), 1)))
    sink_off_live()
    H().live_in_method()
    relayed_call()


main()
