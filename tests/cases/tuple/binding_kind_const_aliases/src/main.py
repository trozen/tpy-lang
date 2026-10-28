# A binding into a const source is const, and so is every alias of it;
# each section writes the aliased object through a second parameter.
from tpy import Own, readonly


class Box:
    v: int

    def __init__(self, v: int) -> None:
        self.v = v


class Holder:
    inner: Box

    def __init__(self, inner: Own[Box]) -> None:
        self.inner = inner


class Pairs:
    items: list[tuple[Box, int]]

    def __init__(self, v: int) -> None:
        self.items = [(Box(v), 1)]

    def bump(self) -> None:
        first, n = self.items[0]
        first.v += 10


class RCtx:
    b: Box

    def __init__(self, v: int) -> None:
        self.b = Box(v)

    @readonly
    def __enter__(self) -> readonly[Box]:
        return self.b

    @readonly
    def __exit__(self, et, ev, tb) -> None:
        pass


# Standalone unpack: an Optional target off an unmutated tuple parameter.
def unpack_opt(t: tuple[Box | None, int], other: Box) -> int:
    a, n = t
    b = a  # tpyc: ok
    other.v += 10
    if b is not None:
        return b.v + n
    return n


# A for head: an Optional target off an unmutated list parameter. It only
# reads.
def head_opt(ts: list[tuple[Box | None, int]]) -> int:
    s = 0
    for a, n in ts:
        b = a  # tpyc: ok
        if b is not None:
            s += b.v + n
    return s


# Standalone unpack: a record target aliasing an unmutated tuple's element.
def unpack_ref(t: tuple[Box, int], other: Box) -> int:
    a, n = t
    d = a  # tpyc: ok
    other.v += 10
    return d.v + n


# Match: a keyword capture of an unmutated subject's field.
def match_ref(h: Holder, other: Box) -> int:
    match h:
        case Holder(inner=q):
            r = q  # tpyc: ok
            other.v += 10
            return r.v
    return -1


# with: a target off a readonly `__enter__`, aliased in the body.
def with_alias(r: RCtx, other: Box) -> int:
    with r as w:
        x = w  # tpyc: ok
        other.v += 10
        t = x.v
    return t


# match: a capture of a readonly subject, aliased in both branches.
def match_branch(h: readonly[Holder], other: Box, f: bool) -> int:
    match h:
        case Holder(inner=q):
            if f:
                r = q  # tpyc: ok
            else:
                r = q  # tpyc: ok
            other.v += 10
            return r.v
    return -1


# for head: a target over an unmutated list, aliased in both branches.
def head_branch(ts: list[tuple[Box, int]], owner: Pairs, f: bool) -> int:
    s = 0
    for a, n in ts:
        if f:
            d = a  # tpyc: ok
        else:
            d = a  # tpyc: ok
        owner.bump()
        s += d.v + n
    return s


# for head: a target over a reference alias of an unmutated list.
def alias_head(ts: list[tuple[Box, int]], owner: Pairs) -> int:
    xs = ts
    s = 0
    for a, n in xs:
        d = a  # tpyc: ok
        owner.bump()
        s += d.v
    return s


# Plain reseat: one alias re-pointed from one const target to another.
def reseat(t: readonly[tuple[Box, int]], u: readonly[tuple[Box, int]],
           other: Box) -> int:
    a, n = t
    b, m = u
    d = a
    s = d.v
    d = b  # tpyc: ok
    other.v += 10
    return s + d.v


# try: an alias of an unpack target hoisted out of the try body.
def try_alias(t: tuple[Box, int], other: Box) -> int:
    a, n = t
    try:
        d = a  # tpyc: ok
        other.v += 10
    finally:
        n += 1
    return d.v + n


# match: the whole Optional subject captured as its pointer, aliased.
def subject_alias(p: Box | None, other: Box) -> int:
    match p:
        case Box(v=0):
            return -1
        case q:
            r = q  # tpyc: ok
            other.v += 10
            if r is not None:
                return r.v
            return 0


# match: a capture read after the match, so hoisted out of its arm.
def hoisted_capture(h: Holder, other: Box) -> int:
    match h:
        case Holder(inner=q):  # tpyc: ok
            pass
    other.v += 10
    return q.v


# A chain of aliases, each hoisted by its own `try`: const to its end.
def alias_chain(t: tuple[Box, int], other: Box) -> int:
    a, n = t
    try:
        d1 = a
    finally:
        n += 1
    try:
        d2 = d1
    finally:
        n += 1
    try:
        d3 = d2
    finally:
        n += 1
    try:
        d4 = d3
    finally:
        n += 1
    try:
        d5 = d4  # tpyc: ok
    finally:
        n += 1
    other.v += 10
    return d5.v + n


# A const alias in one block and a later, separate binding of the name
# that is written through: two C++ variables, so neither is refused.
def two_slots(p: readonly[Box], c: bool) -> int:
    if c:
        alias = p
        n = alias.v
    else:
        n = 0
    alias = Box(5)
    alias.v += 1  # tpyc: ok
    return n + alias.v


# A const alias in one block and a later, separate binding of the name
# re-pointed between two mutable locals: the later slot is `Box*`.
def later_slot_repointed(p: readonly[Box], c: bool) -> int:
    s = 0
    if c:
        alias = p
        s = alias.v
    x = Box(1)
    y = Box(2)
    alias = x
    alias = y  # tpyc: ok
    y.v += 10  # `alias` is `y`: the read below sees it
    alias.v += 100
    return s * 1000 + alias.v + y.v


# with: two targets of one name, each a readonly `__enter__` result: one
# const pointer re-pointed between them.
def with_targets(r: RCtx, r2: RCtx, other: Box) -> int:
    with r as w:
        s = w.v
    with r2 as w:  # tpyc: ok
        pass
    other.v += 10  # `other` is r2's box: w aliases it
    return s * 100 + w.v


# A readonly record-union parameter copied into a local: a const pointer
# union aliasing the caller's object.
def readonly_union_copy(current: readonly[Box | Holder], other: Box) -> int:
    saved = current  # tpyc: ok
    other.v = 12  # `other` is the caller's box: saved aliases it
    if isinstance(saved, Box):
        return saved.v
    return 0


# The same with the per-member spelling.
def readonly_union_copy_alt(current: readonly[Box] | readonly[Holder],
                            other: Box) -> int:
    saved = current  # tpyc: ok
    other.v = 12  # `other` is the caller's box: saved aliases it
    if isinstance(saved, Box):
        return saved.v
    return 0


# A pointer-union local re-seated between two readonly union parameters:
# the union reseat keeps the const pointer union.
def readonly_union_reseat(c1: readonly[Box | Holder],
                          c2: readonly[Box | Holder], other: Box,
                          f: bool) -> int:
    saved = c1
    if f:
        saved = c2  # tpyc: ok
    other.v = 13  # `other` is c2's box: saved aliases it
    if isinstance(saved, Box):
        return saved.v
    return 0


# with, method position: two readonly targets of one name in a method.
class WithMethod:
    k: int

    def __init__(self) -> None:
        self.k = 0

    def targets(self, r: RCtx, r2: RCtx, other: Box) -> int:
        with r as w:
            s = w.v
        with r2 as w:  # tpyc: ok
            pass
        other.v += 10  # `other` is r2's box: w aliases it
        return s * 100 + w.v


def main() -> None:
    b = Box(1)
    print("unpack_opt:", unpack_opt((b, 2), b), unpack_opt((None, 5), b))
    print("head_opt:", head_opt([(Box(3), 4), (None, 5)]))
    c = Box(7)
    print("unpack_ref:", unpack_ref((c, 1), c))
    h = Holder(Box(4))
    print("match_ref:", match_ref(h, h.inner))
    rc = RCtx(4)
    print("with_alias:", with_alias(rc, rc.b))
    h = Holder(Box(4))
    print("match_branch:", match_branch(h, h.inner, True),
          match_branch(h, h.inner, False))
    p = Pairs(1)
    print("head_branch:", head_branch(p.items, p, False))
    p2 = Pairs(1)
    print("alias_head:", alias_head(p2.items, p2))
    b1 = Box(1)
    b2 = Box(5)
    print("reseat:", reseat((b1, 2), (b2, 1), b2))
    c = Box(1)
    print("try_alias:", try_alias((c, 2), c))
    e = Box(2)
    print("subject_alias:", subject_alias(e, e))
    h2 = Holder(Box(3))
    print("hoisted_capture:", hoisted_capture(h2, h2.inner))
    d = Box(1)
    print("alias_chain:", alias_chain((d, 2), d))
    print("two_slots:", two_slots(Box(3), True), two_slots(Box(3), False))
    print("later_slot_repointed:", later_slot_repointed(Box(3), True),
          later_slot_repointed(Box(3), False))
    rc1 = RCtx(1)
    rc2 = RCtx(2)
    print("with_targets:", with_targets(rc1, rc2, rc2.b))
    bx = Box(1)
    bx2 = Box(1)
    print("readonly_union_copy:", readonly_union_copy(bx, bx),
          readonly_union_copy_alt(bx2, bx2))
    b2 = Box(2)
    print("readonly_union_reseat:",
          readonly_union_reseat(Holder(Box(0)), b2, b2, True))
    rc3 = RCtx(1)
    rc4 = RCtx(2)
    print("with_targets_method:", WithMethod().targets(rc3, rc4, rc4.b))



main()
