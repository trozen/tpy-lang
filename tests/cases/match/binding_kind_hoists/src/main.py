# A match binding reads the record the planner decides for its name; each
# section writes the bound object afterwards.
import asyncio
from typing import Iterator

from tpy import Own, readonly


class Inner:
    n: int

    def __init__(self, n: int) -> None:
        self.n = n


class Holder:
    inner: Inner

    def __init__(self, n: int) -> None:
        self.inner = Inner(n)

    def on_self(self, g: "Holder") -> int:
        # on_self: the outer match is on the receiver, the nested one (in a
        # for body) re-seats `q` off `g`; both are written.
        match self:
            case Holder(inner=q):
                self.inner.n = 4
                for _ in range(1):
                    match g:  # tpyc: ok
                        case Holder(inner=q):
                            pass
                g.inner.n = 99
                q.n += 1
                return q.n
        return -1


class Slot:
    inner: Inner
    opt: Inner | None

    def __init__(self, n: int) -> None:
        self.inner = Inner(n)
        self.opt = Inner(n + 1)


class Src:
    p: Inner

    def __init__(self, n: int) -> None:
        self.p = Inner(n)

    def view(self) -> readonly[Inner]:
        return self.p


def mk(n: int) -> Own[Inner]:
    return Inner(n)


def mko(s: Slot) -> Inner | None:
    return s.opt


def same_field(h: Holder, g: Holder, alias: Holder) -> int:
    # same_field: both arms alias one field of `g`; `v` is the pointer local
    # an if chain would declare.
    match h:
        case Holder(inner=Inner(n=1)):
            v = g.inner  # tpyc: ok
        case _:
            v = g.inner
    alias.inner.n = 11
    return v.n


def const_capture(h: readonly[Holder], k: readonly[Holder],
                  alias: Holder) -> int:
    # const_capture: one arm aliases `k`, the other captures out of the
    # read-only subject: one const pointer for both.
    match h:
        case Holder(inner=Inner(n=1)):
            q = k.inner  # tpyc: ok
        case Holder(inner=q):
            pass
    alias.inner.n = 12
    return q.n


def owned_or_field(h: Holder, alias: Holder) -> int:
    # owned_or_field: one arm owns a fresh record, the other aliases a
    # field: the pointer local over a slot the owning arm fills.
    match h:
        case Holder(inner=Inner(n=1)):
            v = mk(1)  # tpyc: ok
        case _:
            v = h.inner
    v.n += 1
    alias.inner.n += 100
    return v.n


def nullable_or_none(h: Holder, g: Slot, alias: Slot) -> int:
    # nullable_or_none: a nullable alias of `g`'s field in one arm, None in
    # the other.
    match h:
        case Holder(inner=Inner(n=1)):
            o = g.opt  # tpyc: ok
        case _:
            o = None
    a = alias.opt
    if a is not None:
        a.n = 13
    if o is not None:
        return o.n
    return 0


def nullable_rebound(h: Holder, s: Slot) -> int:
    # nullable_rebound: a nullable alias bound from a field in one arm and
    # from a call in the other.
    match h:
        case Holder(inner=Inner(n=1)):
            o = s.opt  # tpyc: ok
        case _:
            o = mko(s)
    if o is not None:
        o.n += 1
    a = s.opt
    if a is not None:
        return a.n
    return 0


def readonly_one(h: Holder, r: Src) -> int:
    # readonly_one: a readonly-typed result bound in one arm only.
    match h:
        case Holder(inner=Inner(n=1)):
            v = r.view()  # tpyc: ok
        case _:
            return 0
    r.p.n = 14
    return v.n


def readonly_both(h: Holder, r: Src) -> int:
    # readonly_both: a readonly-typed result bound in both arms.
    match h:
        case Holder(inner=Inner(n=1)):
            v = r.view()  # tpyc: ok
        case _:
            v = r.view()
    r.p.n = 15
    return v.n


def try_capture(o: Inner | None) -> int:
    # try_capture: a capture of a name the enclosing `try` hoists writes that
    # pointer, so a read after the statement sees what the capture bound.
    total = 0
    try:
        match o:
            case v:  # tpyc: ok
                if v is not None:
                    v.n += 100
                    total = v.n
    finally:
        total += 1
    if v is not None:
        v.n += 1000
        total += v.n
    return total


def existing_local(o: Inner | None) -> int:
    # existing_local: a capture of a local declared before the match writes
    # that local.
    v: Inner | None = None
    match o:
        case v:  # tpyc: ok
            pass
    if v is not None:
        v.n += 1
        return v.n
    return 0


def guard_binds(o: Inner | None, p: Inner | None, c: bool) -> int:
    # guard_binds: a capture whose guard fails has still bound the local.
    v = p
    match o:
        case v if c:  # tpyc: ok
            pass
        case _:
            pass
    if v is not None:
        v.n += 100
        return v.n
    return 0


def nested_recapture(a: Inner | None, b: Inner | None) -> int:
    # nested_recapture: a nested match re-captures the outer capture's name.
    match a:
        case v:
            match b:
                case v:  # tpyc: ok
                    pass
            if v is not None:
                v.n += 100
                return v.n
    return 0


def gen_capture(o: Inner | None, p: Inner | None) -> Iterator[int]:
    # gen_capture: in a generator, the capture writes the local across the
    # suspension.
    v = p
    match o:
        case v:  # tpyc: ok
            pass
    yield 1
    if v is not None:
        v.n += 100
    yield 2


async def async_capture(o: Inner | None, p: Inner | None) -> int:
    # async_capture: the same in a coroutine frame.
    v = p
    match o:
        case v:  # tpyc: ok
            pass
    await asyncio.sleep(0)
    if v is not None:
        v.n += 100
    return 0


class Ctx:
    b: Inner

    def __init__(self, n: int) -> None:
        self.b = Inner(n)

    def __enter__(self) -> Inner:
        return self.b

    def __exit__(self, et, ev, tb) -> None:
        pass


def capture_then_with(r: Ctx, o: Inner | None) -> int:
    # capture_then_with: a `with` target named as an earlier arm's capture
    # binds afresh.
    match o:
        case Inner() as w:
            w.n += 1
        case _:
            pass
    with r as w:  # tpyc: ok
        w.n += 2
    w.n += 10
    return r.b.n


def capture_again(u: int, w: int) -> int:
    # capture_again: a second match's capture of an ended arm's name.
    match u:
        case 1:
            pass
        case x:
            print("capture_again: first", x)
    match w:
        case 2:
            pass
        case x:  # tpyc: ok
            x += 5
            return x
    return 0


def plain_after(o: Inner | None, q: Inner) -> int:
    # plain_after: a plain rebind of an ended arm's capture binds afresh.
    match o:
        case Inner() as w:
            w.n += 1
        case _:
            pass
    w = q  # tpyc: ok
    w.n += 7
    return q.n


def for_after(o: Inner | None, xs: list[Inner]) -> int:
    # for_after: a for head named as an ended arm's capture binds afresh.
    match o:
        case Inner() as w:
            w.n += 1
        case _:
            pass
    for w in xs:  # tpyc: ok
        w.n += 2
    return xs[0].n


def nested_container(h: Holder, g: Holder) -> int:
    # nested_container: a nested match's arm leaks a container read after
    # both matches.
    match h:
        case Holder(inner=q):
            match g:  # tpyc: ok
                case Holder():
                    xs = [1, 2]
            xs.append(3)
            return q.n + len(xs)
    return 0


def match_in_if(h: Holder, c: bool) -> int:
    # match_in_if: a match inside an if branch hoists a record alias read
    # after the match.
    if c:
        match h:  # tpyc: ok
            case Holder(inner=Inner(n=1)):
                v = h.inner
            case _:
                v = Inner(7)
        v.n += 100
        return v.n
    return 0


def match_in_loop(hs: list[Holder]) -> int:
    # match_in_loop: the same inside a loop body.
    t = 0
    for h in hs:
        match h:  # tpyc: ok
            case Holder(inner=Inner(n=1)):
                v = h.inner
            case _:
                v = Inner(7)
        v.n += 100
        t += v.n
    return t


def match_in_handler(h: Holder) -> int:
    # match_in_handler: a match inside an except handler hoists a record
    # alias read after the match.
    try:
        raise ValueError("x")
    except ValueError:
        match h:  # tpyc: ok
            case Holder(inner=Inner(n=1)):
                v = h.inner
            case _:
                v = Inner(7)
        v.n += 100
    return h.inner.n


def match_in_while(h: Holder) -> int:
    # match_in_while: the same inside a while body.
    k = 0
    while k < 1:
        match h:  # tpyc: ok
            case Holder(inner=Inner(n=1)):
                v = h.inner
            case _:
                v = Inner(7)
        v.n += 100
        k += 1
    return h.inner.n


def reseat_in_if(h: Holder, g: Holder, c: bool) -> int:
    # reseat_in_if: a nested re-seat inside an if in the outer arm; both
    # subjects are written, so the shared slot is mutable.
    match h:
        case Holder(inner=q):
            h.inner.n = 6
            if c:
                match g:  # tpyc: ok
                    case Holder(inner=q):
                        pass
            g.inner.n += 10
            q.n += 1
            return q.n
    return -1


def reseat_in_while(h: Holder, g: Holder) -> int:
    # reseat_in_while: the same inside a while body.
    match h:
        case Holder(inner=q):
            h.inner.n = 8
            while True:
                match g:  # tpyc: ok
                    case Holder(inner=q):
                        pass
                break
            g.inner.n = 98
            q.n += 1
            return q.n
    return -1


def container_existing(h: Holder, g: Holder, keep: list[list[int]]) -> int:
    # container_existing: the nested match's hoisted container aliases an
    # existing list in one arm, and is written through.
    t = 0
    for i in range(2):
        match h:
            case Holder(inner=q):
                match g:  # tpyc: ok
                    case Holder(inner=Inner(n=2)):
                        xs = keep[0]
                    case _:
                        xs = [1, 2]
                xs.append(i)
                t += q.n + len(xs)
    return t


class Narrow:
    k: int

    def __init__(self) -> None:
        self.k = 0

    def bump(self, o: Inner | None) -> int:
        # narrowed_method: a narrowed Optional's capture in a method, written
        # through and read back via the Optional.
        if o is not None:
            match o:  # tpyc: ok
                case Inner() as v:
                    v.n += 1
            return o.n
        return 0


def main() -> None:
    g = Holder(2)
    print("same_field:", same_field(Holder(1), g, g))
    k = Holder(2)
    hh = Holder(5)
    print("const_capture:", const_capture(Holder(1), k, k),
          const_capture(hh, k, hh))
    h1 = Holder(1)
    h5 = Holder(5)
    print("owned_or_field:", owned_or_field(h1, h1), owned_or_field(h5, h5))
    sg = Slot(2)
    print("nullable_or_none:", nullable_or_none(Holder(1), sg, sg),
          nullable_or_none(Holder(5), sg, sg))
    s1 = Slot(1)
    s2 = Slot(1)
    print("nullable_rebound:", nullable_rebound(Holder(1), s1),
          nullable_rebound(Holder(5), s2))
    r = Src(3)
    print("readonly_one:", readonly_one(Holder(1), r),
          readonly_one(Holder(5), r))
    print("readonly_both:", readonly_both(Holder(1), Src(3)),
          readonly_both(Holder(5), Src(3)))
    b = Inner(3)
    print("try_capture:", try_capture(b), b.n)
    e = Inner(7)
    print("existing_local:", existing_local(e), e.n)
    o = Inner(1)
    p = Inner(2)
    print("guard_binds:", guard_binds(o, p, False), guard_binds(o, p, True),
          o.n, p.n)
    a = Inner(1)
    b = Inner(2)
    print("nested_recapture:", nested_recapture(a, b), a.n, b.n)
    a = Inner(1)
    b = Inner(2)
    for _ in gen_capture(a, b):
        pass
    print("gen_capture:", a.n, b.n)
    a = Inner(1)
    b = Inner(2)
    asyncio.run(async_capture(a, b))
    print("async_capture:", a.n, b.n)
    o = Inner(3)
    print("capture_then_with:", capture_then_with(Ctx(1), o), o.n)
    again = capture_again(3, 4)
    print("capture_again:", again)
    o = Inner(1)
    q = Inner(3)
    print("plain_after:", plain_after(o, q), o.n, q.n)
    o = Inner(1)
    xs = [Inner(10)]
    print("for_after:", for_after(o, xs), o.n)
    print("nested_container:", nested_container(Holder(1), Holder(2)))
    h = Holder(1)
    print("match_in_if:", match_in_if(h, True), h.inner.n)
    hs = [Holder(1), Holder(2)]
    print("match_in_loop:", match_in_loop(hs), hs[0].inner.n, hs[1].inner.n)
    print("match_in_handler:", match_in_handler(Holder(1)))
    print("match_in_while:", match_in_while(Holder(1)))
    g = Holder(2)
    taken = reseat_in_if(Holder(1), g, True)
    g2 = Holder(2)
    h = Holder(1)
    print("reseat_in_if:", taken, g.inner.n, reseat_in_if(h, g2, False),
          h.inner.n, g2.inner.n)
    g = Holder(2)
    print("reseat_in_while:", reseat_in_while(Holder(1), g), g.inner.n)
    g = Holder(2)
    print("on_self:", Holder(1).on_self(g), g.inner.n)
    keep = [[7]]
    print("container_existing:",
          container_existing(Holder(1), Holder(2), keep), keep)
    ob = Inner(3)
    print("narrowed_method:", Narrow().bump(ob), ob.n)


main()
