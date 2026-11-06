# A callable's return reads as a def's: a bare reference-type return is a
# borrow of the call's arguments (aliases, as `ident(x)` does), Own[R] a fresh
# value. Every section writes through the result and prints the source.
from typing import Callable
from tpy import Own, int32
import cdr_helpers
from fixtures import P, Echo, Lender, ident, mk, via, via_c, ret, own, own_slot
import part_positions
import part_readonly
import part_generic
import part_capture


# free function: through fixtures' via / via_c / ret / own / own_slot.
def sec_free() -> None:
    p = P(1)
    via(ident, p)
    via_c(ident, p)
    via(lambda q: q, p)
    via(Echo(), p)
    ret(ident, p).n += 1
    print("free:", p.n)
    # The copy is warned, so this leg only reads it (CPython aliases).
    o = own(ident, p)
    m = own_slot(mk, p)
    m.n += 100
    print("free own:", p.n, o.n, m.n)


# lambda at a Callable slot takes the contract: `-> P&`, aliases.
def sec_lambda() -> None:
    p = P(1)
    k: Callable[[P], P] = lambda q: q  # tpyc: ok
    r = k(p)
    r.n += 5
    t: Callable[[Lender], P] = lambda o: o.p  # tpyc: ok
    s = Lender()
    t(s).n += 1
    g: Callable[[int32], Own[P]] = lambda i: P(i)  # tpyc: ok
    print("lambda:", p.n, s.p.n, g(3).n)
    # a call through a captured Callable[[P], P] roots in its argument.
    cb: Callable[[P], P] = ident
    kc: Callable[[P], P] = lambda q: cb(q)  # tpyc: warning(/may keep its own copy of 'cb'/)
    x = P(1)
    kc(x).n += 10
    print("lambda captured callable:", x.n)


# a module function, a static method and a classmethod are called with no
# receiver, so their result lends the argument alone.
class Util:
    @staticmethod
    def same(p: P) -> P:
        return p

    @classmethod
    def same_c(cls, p: P) -> P:
        return p


G_P = P(500)


def global_p() -> P:
    return G_P


def bigger_of(a: P, b: P) -> P:
    return a if a.n > b.n else b


def sec_receivers() -> None:
    q = cdr_helpers.Q(1)
    km: Callable[[cdr_helpers.Q], cdr_helpers.Q] = lambda x: cdr_helpers.same_q(x)  # tpyc: ok
    km(q).n += 1
    p = P(1)
    ks: Callable[[P], P] = lambda x: Util.same(x)  # tpyc: ok
    kc: Callable[[P], P] = lambda x: Util.same_c(x)  # tpyc: ok
    ks(p).n += 10
    kc(p).n += 100
    print("receivers:", q.n, p.n)
    # a zero-argument callee's result lends module storage, not a capture.
    kg: Callable[[P], P] = lambda x: bigger_of(global_p(), x)  # tpyc: ok
    kg(p).n += 1000
    print("receivers global:", p.n, G_P.n)


def main() -> None:
    sec_free()
    sec_lambda()
    part_positions.main()
    part_readonly.main()
    part_generic.main()
    part_capture.main()
    sec_receivers()


main()
