# The part of an @auto_readonly result that follows the receiver is read off
# the declaration: a whole-result marker (`auto_readonly[T]`) and a component
# marker (`Ptr[auto_readonly[T]]`) both keep the receiver mutable where the
# result is written through, whatever the body reads the payload through: a
# reference result loans the receiver to what binds it, a returned handle
# demotes the receiver at the call.
from tpy import Ptr, int32, auto_readonly, readonly
from tplib.array_list import ArrayList


class Sub:
    n: int32

    def __init__(self) -> None:
        self.n = 0


class Item:
    n: int32
    sub: Sub

    def __init__(self) -> None:
        self.n = 0
        self.sub = Sub()


class Payload:
    item: Item
    items: list[Item]

    def __init__(self) -> None:
        self.item = Item()
        self.items = [Item()]


class Handle:
    p: Ptr[Payload]

    def __init__(self, p: Payload) -> None:
        self.p = p

    # root: the whole result follows the receiver, reached through a Ptr
    @auto_readonly
    def get(self) -> auto_readonly[Item]:
        it = self.p.item
        return it

    # component: the pointee of the returned Ptr follows the receiver
    @auto_readonly
    def ptr(self) -> Ptr[auto_readonly[Payload]]:
        return self.p

    # unmarked: a returned Ptr with no marker points with its own access
    @auto_readonly
    def raw(self) -> Ptr[Payload]:
        return self.p

    # chain: the receiver itself, for a call chained off it
    @auto_readonly
    def me(self) -> auto_readonly["Handle"]:
        return self

    # loop: a whole container result, reached through a Ptr
    @auto_readonly
    def all(self) -> auto_readonly[list[Item]]:
        xs = self.p.items
        return xs


# root: a write through the result keeps the receiver mutable
def root_write(h: Handle) -> None:
    h.get().n += 1


# root_read: a read keeps it const
def root_read(h: Handle) -> int32:
    return h.get().n


# root_alias_read: a local bound to the result and only read keeps it const
def root_alias_read(h: Handle) -> int32:
    x = h.get()  # tpyc: ok
    return x.n


# root_walrus: a write through a walrus-bound result climbs its loan
def root_walrus(h: Handle) -> None:
    if (x := h.get()).n >= 0:  # tpyc: ok
        x.n += 1


# root_derived: a place derived from the result (a field of it), written
def root_derived(h: Handle) -> None:
    x = h.get().sub  # tpyc: ok
    x.n += 10


# root_escape: the result returned at a mutable slot hands the caller write
# (the caller's `root_escape(h).n += 1` evaluates the call twice,
# BUGS.md#augassign-call-receiver-double-eval; the call has no side effect)
def root_escape(h: Handle) -> Item:
    return h.get()  # tpyc: ok


def take_ro(x: readonly[Item]) -> int32:
    return x.n


# root_ro_arg: the result passed to a readonly parameter keeps it const
def root_ro_arg(h: Handle) -> int32:
    return take_ro(h.get())  # tpyc: ok


# chain_write: a receiver that is itself a receiver-following call lends ITS
# receiver, so a write through the bound result keeps `h` mutable
def chain_write(h: Handle) -> None:
    x = h.me().get()  # tpyc: ok
    x.n += 1


# chain_read: the same chain only read keeps `h` const and binds const
def chain_read(h: Handle) -> int32:
    x = h.me().get()  # tpyc: ok
    return x.n


# select_write: a select receiver lends each operand it may pick
def select_write(h: Handle, g: Handle, c: bool) -> None:
    x = (h if c else g).get()  # tpyc: ok
    x.n += 1


# loop_write: a loop over the result writes the receiver's storage
def loop_write(h: Handle) -> None:
    for x in h.all():  # tpyc: ok
        x.n += 1


# loop_read: the same loop only read keeps it const
def loop_read(h: Handle) -> int32:
    t = 0
    for x in h.all():  # tpyc: ok
        t += x.n
    return t


# component_inline: a write through the returned Ptr
def component_inline(h: Handle) -> None:
    h.ptr().item.n += 10


# component_alias: a write through a local bound to the returned Ptr
def component_alias(h: Handle) -> None:
    q = h.ptr()
    q.item.n += 100


# component_read: the call lends a writable Ptr, which nothing tracks once
# copied, so it keeps the receiver mutable even for a read
def component_read(h: Handle) -> int32:
    return h.ptr().item.n


def set_n(p: Ptr[Payload]) -> None:
    p.item.n += 1000


# component_arg: the returned Ptr passed to a mutable Ptr parameter keeps the
# receiver mutable
def component_arg(h: Handle) -> None:
    set_n(h.ptr())


# component_nullable: the alias tested for None before it is written through
def component_nullable(h: Handle) -> None:
    q = h.ptr()
    if q is not None:
        q.item.n += 10000


# unmarked: a write through an unmarked returned Ptr keeps the receiver const
def unmarked_write(h: Handle) -> None:
    h.raw().item.n += 100000


# unmarked_alias: the same through a bound copy of the Ptr
def unmarked_alias(h: Handle) -> None:
    q = h.raw()  # tpyc: ok
    q.item.n += 1


# getitem: a subscript dispatched to an @auto_readonly __getitem__ lending a
# Ptr is the same call
class Rows:
    ps: list[Ptr[Item]]

    def __init__(self, it: Ptr[Item]) -> None:
        self.ps = []
        self.ps.append(it)

    @auto_readonly
    def __getitem__(self, i: int32) -> Ptr[auto_readonly[Item]]:
        return self.ps[i]

    @auto_readonly
    def at(self, i: int32) -> Ptr[auto_readonly[Item]]:
        return self.ps[i]


def getitem_alias(r: Rows) -> None:
    q = r[0]  # tpyc: ok
    q.n += 1000000


def getitem_inline(r: Rows) -> None:
    r[0].n += 10000000  # tpyc: ok


# getitem_ro_read: a readonly receiver runs the const clone and reads
def getitem_ro_read(r: readonly[Rows]) -> int32:
    return r[0].n  # tpyc: ok


# self_receiver: the call lending a Ptr demotes `self` in the method making it
class Owner:
    rows: Rows

    def __init__(self, it: Ptr[Item]) -> None:
        self.rows = Rows(it)

    def bump_sub(self) -> None:
        q = self.rows[0]  # tpyc: ok
        q.n += 1

    def bump_at(self) -> None:
        q = self.rows.at(0)  # tpyc: ok
        q.n += 10


# loop_exemption: a `for` over an ArrayList takes the Span its `__iter__`
# lends only through the loop variable, so a read keeps the list const; a
# comprehension variable does not climb to its source
# (BUGS.md#comprehension-loop-var-mutation-not-propagated), so the same read
# there takes no exemption and the lending call demotes the list
def loop_exempt_read(al: ArrayList[int32, 4]) -> int32:
    t = 0
    for v in al:  # tpyc: ok
        t += v
    return t


def comp_read(al: ArrayList[int32, 4]) -> int32:
    return sum([v for v in al])  # tpyc: ok


def main() -> None:
    ps = [Payload()]
    h = Handle(ps[0])
    root_write(h)
    print("root", root_read(h))
    component_inline(h)
    component_alias(h)
    print("component", component_read(h), ps[0].item.n)
    component_arg(h)
    print("component_arg", ps[0].item.n)
    component_nullable(h)
    print("component_nullable", ps[0].item.n)
    unmarked_write(h)
    print("unmarked", ps[0].item.n)
    root_walrus(h)
    root_derived(h)
    root_escape(h).n += 1
    print("root_alias", root_alias_read(h), ps[0].item.sub.n)
    r = Rows(ps[0].item)
    getitem_alias(r)
    getitem_inline(r)
    print("getitem", ps[0].item.n)
    print("getitem_ro_read", getitem_ro_read(r))
    print("root_ro_arg", root_ro_arg(h))
    unmarked_alias(h)
    print("unmarked_alias", ps[0].item.n)
    p2 = Payload()
    h2 = Handle(p2)
    chain_write(h2)
    print("chain", chain_read(h2), p2.item.n)
    select_write(h2, h, True)
    print("select", p2.item.n)
    loop_write(h2)
    print("loop", loop_read(h2), p2.items[0].n)
    o = Owner(p2.item)
    o.bump_sub()
    o.bump_at()
    print("self_receiver", p2.item.n)
    al = ArrayList[int32, 4]()
    al.append(3)
    al.append(4)
    print("loop_exemption", loop_exempt_read(al), comp_read(al))


main()
