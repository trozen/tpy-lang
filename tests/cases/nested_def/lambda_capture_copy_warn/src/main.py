# An escaping (Callable) closure copies each capture when it is created; the
# warnings name every divergence from CPython's late-binding closure, one
# section per position. Each section mutates or rebinds after creation.
from typing import Callable
from tpy import int32, Own
from fixtures import P
import part_comp_self
import part_hedge
import part_loops
import part_positions
import part_tuple


class Holder:
    fs: list[Callable[[], int32]]

    def __init__(self) -> None:
        self.fs = []

    def keep(self, f: Callable[[], int32]) -> None:
        self.fs.append(f)


def call_now(f: Callable[[], int32]) -> int32:
    return f()


# local: a list local the lambda copies
def local() -> None:
    xs = [1, 2]
    read: Callable[[], int32] = lambda: len(xs)  # tpyc: warning(/lambda at line 31 copies the object 'xs' refers to/)
    xs.append(3)
    print("local:", read())


# alias / elem: an alias of a local and a borrowed container element
def alias_elem(ps: list[P]) -> None:
    xs = [1, 2]
    ys = xs
    p = ps[0]
    read: Callable[[], int32] = lambda: len(ys) + p.n  # tpyc: warning(/copies the object 'ys'/) warning(/copies the object 'p'/)
    xs.append(3)
    ps[0].n = 5
    print("alias_elem:", read())


# param: a borrowed reference parameter
def param(xs: list[int32]) -> Callable[[], int32]:
    return lambda: len(xs)  # tpyc: warning(/copies the object 'xs'/)


# own_param: an Own[T] parameter the lambda copies rather than moves
def own_param(xs: Own[list[int32]]) -> Callable[[], int32]:  # tpyc: warning(/never consumed/)
    f: Callable[[], int32] = lambda: len(xs)  # tpyc: warning(/copies the object 'xs'/)
    xs.append(3)
    return f


# method: a method local (the receiver itself is captured as itself)
class M:
    k: int32

    def __init__(self) -> None:
        self.k = 10

    def make(self) -> Callable[[], int32]:
        xs = [1]
        f: Callable[[], int32] = lambda: len(xs) + self.k  # tpyc: warning(/copies the object 'xs'/)
        xs.append(2)
        return f


# ctor_field: a lambda stored into a field in __init__ over a parameter
class C:
    getter: Callable[[], int32]

    def __init__(self, p: P) -> None:
        self.getter = lambda: p.n  # tpyc: warning(/copies the object 'p'/)


# ret / store / arg: returned, appended to a list, stored by a callee
def ret_store_arg(h: Holder) -> Callable[[], int32]:
    xs = [1]
    fs: list[Callable[[], int32]] = []
    fs.append(lambda: len(xs) * 10)  # tpyc: warning(/copies the object 'xs'/)
    h.keep(lambda: len(xs) * 100)  # tpyc: warning(/copies the object 'xs'/)
    out: Callable[[], int32] = lambda: len(xs) * 1000  # tpyc: warning(/copies the object 'xs'/)
    xs.append(2)
    print("store:", fs[0]())
    return out


# comp: lambdas built by a comprehension, over its variable and a local
def comp() -> None:
    xs = [1]
    fs: list[Callable[[], int32]] = [lambda: len(xs) + i for i in range(2)]  # tpyc: warning(/copies the object 'xs'/) warning(/'i' is bound again on each iteration/)
    xs.append(2)
    print("comp:", fs[0](), fs[1]())


# curried: an inner lambda over the enclosing lambda's parameter
def curried() -> Callable[[list[int32]], Callable[[], int32]]:
    return lambda zs: (lambda: len(zs))  # tpyc: warning(/lambda at line 103 copies the object 'zs'/)


# block / loop: a loop-body local, the loop target and an outer value local
def block(ps: list[P]) -> None:
    fs: list[Callable[[], int32]] = []
    k = 0
    for q in ps:
        snap = q
        k += 1
        fs.append(lambda: snap.n + k * 100)  # tpyc: warning(/copies the object 'snap'/) warning(/'snap' is bound again/) warning(/'k' is bound again/)
        fs.append(lambda: q.n)  # tpyc: warning(/copies the object 'q'/) warning(/'q' is bound again/)
    ps[0].n = 9
    print("block:", fs[0](), fs[1](), fs[2](), fs[3]())


def main() -> None:
    local()
    ps = [P(1), P(2)]
    alias_elem(ps)
    xs = [1]
    f = param(xs)
    xs.append(2)
    print("param:", f())
    ys = [1, 2]
    print("own_param:", own_param(ys)())
    m = M()
    g = m.make()
    m.k = 20
    print("method:", g())
    p = P(1)
    c = C(p)
    p.n = 5
    print("ctor_field:", c.getter())
    h = Holder()
    r = ret_store_arg(h)
    print("arg:", h.fs[0](), "ret:", r())
    comp()
    zs = [1]
    inner = curried()(zs)
    zs.append(2)
    print("curried:", inner())
    block([P(1), P(2)])
    part_loops.main()
    part_tuple.main()
    part_positions.main()
    part_hedge.main()
    part_comp_self.main()


main()


# module: a module-level comprehension of lambdas over its variable, a
# reference one, and a lambda over an enclosing module lambda's parameter
mod_fs: list[Callable[[], int32]] = [lambda: i for i in range(2)]  # tpyc: warning(/'i' is bound again on each iteration/)
mod_ps = [P(1), P(2)]
mod_gs: list[Callable[[], int32]] = [lambda: q.n for q in mod_ps]  # tpyc: warning(/copies the object 'q'/) warning(/'q' is bound again/)
mod_curry: Callable[[list[int32]], Callable[[], int32]] = lambda zs: (lambda: len(zs))  # tpyc: warning(/copies the object 'zs'/)
mod_zs = [1]
mod_read = mod_curry(mod_zs)
mod_ps[0].n = 7
mod_zs.append(2)
print("module:", mod_fs[0](), mod_fs[1](), mod_gs[0](), mod_read())


# module_block: a binding in a module-level loop or `if` body is a local of
# the module's init code, not module storage, so the lambda copies it
mod_bfs: list[Callable[[], int32]] = []
for mod_i in range(2):
    mod_x = P(mod_i)
    mod_bfs.append(lambda: mod_x.n)  # tpyc: warning(/copies the object 'mod_x'/) warning(/'mod_x' is bound again/)
    mod_x.n = 10 + mod_i
mod_vfs: list[Callable[[], int32]] = []
for mod_j in range(2):
    mod_v = mod_j * 10
    mod_vfs.append(lambda: mod_v)  # tpyc: warning(/'mod_v' is bound again/)
if len(mod_bfs) > 0:
    mod_y = P(1)
    mod_yf: Callable[[], int32] = lambda: mod_y.n  # tpyc: warning(/copies the object 'mod_y'/)
    mod_y.n = 5
    print("module_block:", mod_bfs[0](), mod_bfs[1](), mod_vfs[0](), mod_vfs[1](), mod_yf())
