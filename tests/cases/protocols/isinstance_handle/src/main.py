# isinstance() tests the value a variable holds: on a Box / Rc / Arc / Weak /
# user __deref__ handle that is the handle, as in CPython, and a check only
# the held object can pass warns. The object is tested by
# name (`p = rc.get()`, `p = deref(rc)`, `match b.get()`, `match deref(b)`).
from typing import Protocol
from tpy import dynamic, deref, Own, Ptr, readonly
from tplib.box import Box
from tplib.rc import Rc, Weak
from tplib.arc import Arc


@dynamic
class Pet(Protocol):
    def name(self) -> str: ...


class Dog(Pet):
    n: int

    def __init__(self) -> None:
        self.n = 0

    def name(self) -> str:
        return "dog"

    @readonly
    def bark(self) -> str:
        return "woof"


class Cat(Pet):
    def __init__(self) -> None:
        pass

    def name(self) -> str:
        return "cat"


class Point:
    x: int

    def __init__(self, x: int) -> None:
        self.x = x


class WeakSub(Weak[Pet]):
    pass


class Animal:
    def __init__(self) -> None:
        pass


class Hound(Animal):
    def __init__(self) -> None:
        super().__init__()

    def upgrade(self) -> int:
        return 1


class Handle:
    target: Point

    def __init__(self, target: Own[Point]) -> None:
        self.target = target

    def __deref__(self) -> Point:
        return self.target


def box_handle(b: Box[Pet], bp: Box[Point]) -> None:
    # A check only the held object can pass tests the handle: False, warned.
    print("box:", isinstance(b, Dog))  # tpyc: warning(/tests the 'Box\[Pet\]' handle, never a 'Dog'; to test the object it holds, bind it first: p = deref\(b\)/)
    print("box:", isinstance(b, Box))  # tpyc: ok
    print("box:", isinstance(bp, Point))  # tpyc: warning(/tests the 'Box\[Point\]' handle/)


def rc_handle(rc: Rc[Pet]) -> None:
    print("rc:", isinstance(rc, Dog))  # tpyc: warning(/tests the 'Rc\[Pet\]' handle, never a 'Dog'/)
    # The handle's own class is what isinstance tests.
    print("rc:", isinstance(rc, Rc))  # tpyc: ok


def arc_handle(a: Arc[Point], ap: Arc[Pet]) -> None:
    print("arc:", isinstance(a, Point))  # tpyc: warning(/tests the 'Arc\[Point\]' handle/)
    print("arc:", isinstance(a, Arc))  # tpyc: ok
    print("arc:", isinstance(ap, Dog))  # tpyc: warning(/tests the 'Arc\[Pet\]' handle, never a 'Dog'/)


def weak_handle(w: Weak[Pet]) -> None:
    # A Weak has no __deref__: an ordinary class, tested as one.
    print("weak:", isinstance(w, Dog), isinstance(w, Weak))  # tpyc: ok


def weak_subclass(ws: WeakSub) -> None:
    # A subclass of a handle without __deref__ is tested as itself too.
    print("weak_subclass:", isinstance(ws, Dog), isinstance(ws, Weak))  # tpyc: ok


def make_weak_sub(rc: Rc[Pet]) -> Own[WeakSub]:
    # The WeakSub holds one more weak reference to rc's cell.
    rc._cell.incr_weak()
    return WeakSub(rc._cell, rc._payload)


def arc_payload(ap: Arc[Pet]) -> None:
    # The held object of an Arc, bound by name: a write reaches ap's.
    p = deref(ap)
    if isinstance(p, Dog):  # tpyc: ok
        p.n += 5
        print("arc_payload:", p.bark(), p.n)


def get_payload(rc: Rc[Pet]) -> None:
    # The held object, bound by name: a borrow, so the write reaches rc's.
    p = rc.get()
    if isinstance(p, Dog):  # tpyc: ok
        p.n += 1
        print("get:", p.bark(), p.n)
    else:
        print("get: other", p.name())


def deref_payload(rc: Rc[Pet]) -> None:
    # deref() of a parameter handle binds a borrow of the object it holds.
    p = deref(rc)
    if isinstance(p, Dog):  # tpyc: ok
        p.n += 10
        print("deref:", p.bark(), p.n)


class Kennel:
    pet: Box[Pet]

    def __init__(self, pet: Own[Box[Pet]]) -> None:
        self.pet = pet

    @readonly
    def show(self) -> None:
        # deref() of a const handle is const: the cast targets `const Dog*`.
        match deref(self.pet):  # tpyc: ok
            case Dog() as d:
                print("deref_readonly:", d.bark())
            case _:
                print("deref_readonly: other")


def deref_record(b: Box[Point]) -> None:
    # A record the handle holds is borrowed too, never copied.
    p = deref(b)
    p.x = 99


def match_get(b: Box[Pet]) -> None:
    match b.get():  # tpyc: ok
        case Dog() as d:
            print("match_get:", d.bark())
        case _:
            print("match_get: other")


def match_deref(b: Box[Pet]) -> None:
    match deref(b):  # tpyc: ok
        case Dog() as d:
            print("match_deref:", d.bark())
        case Cat() as c:
            print("match_deref:", c.name())
        case _:
            print("match_deref: other")


def box_get(b: Box[Pet]) -> str:
    pet = b.get()
    if isinstance(pet, Dog):  # tpyc: ok
        return "box_get: dog " + pet.bark()
    return "box_get: other " + pet.name()


def shadow_alias(b: Box[Pet]) -> None:
    # A user local spelled like the narrow's cast pointer (`__p_ptr`) is
    # not shadowed by it: the branch reads both.
    p = b.get()
    __p_ptr = 21
    if isinstance(p, Dog):  # tpyc: ok
        print("shadow_alias:", p.bark(), __p_ptr)
    else:
        print("shadow_alias: other", p.name(), __p_ptr)


def rebind_handle() -> str:
    b: Box[Pet] = Box(Dog())
    p = b.get()
    if isinstance(p, Dog):
        # Rebinding the handle replaces what it holds: b.get() reads the
        # new object, never the narrowed Dog.
        b = Box(Cat())
        return "rebind_handle: " + b.get().name()
    return "rebind_handle: unreachable"


def match_local(b: Box[Pet]) -> None:
    # A match over the @dynamic local the held object is bound to.
    p = b.get()
    match p:  # tpyc: ok
        case Dog() as d:
            print("match_local:", d.bark())
        case _:
            print("match_local: other")


class Holder:
    b: Box[Pet]

    def __init__(self, b: Own[Box[Pet]]) -> None:
        self.b = b


def match_call_mutates(h: Holder) -> None:
    # The subject borrows h.b's object: replacing h.b in the arm warns.
    match h.b.get():
        case Dog() as d:
            print("match_call_mutates:", d.bark())
            h.b = Box(Cat())  # tpyc: warning(/'h.b' is mutated in this arm while pattern bindings borrow its storage/)
        case _:
            print("match_call_mutates: other")


def optional_box(ob: Box[Pet] | None) -> None:
    if ob is None:
        print("optional_box: none")
        return
    print("optional_box:", isinstance(ob, Dog))  # tpyc: warning(/tests the 'Box\[Pet\]' handle/)
    p = ob.get()
    if isinstance(p, Dog):  # tpyc: ok
        print("optional_box:", p.bark())


def upgrade_method(h: Hound) -> None:
    # A method named `upgrade` makes no class a handle.
    print("upgrade:", isinstance(h, Animal))  # tpyc: ok


def user_deref(h: Handle) -> None:
    print("user_deref:", isinstance(h, Handle))  # tpyc: ok
    print("user_deref:", isinstance(h, Point))  # tpyc: warning(/tests the 'Handle' handle, never a 'Point'/)


def ptr_object(p: Ptr[Point]) -> None:
    # A Ptr is the object itself in CPython, not a handle.
    print("ptr:", isinstance(p, Point))  # tpyc: ok


def main() -> None:
    b: Box[Pet] = Box(Dog())
    bp = Box(Point(1))
    box_handle(b, bp)
    rc: Rc[Pet] = Rc.new(Dog())
    rc_handle(rc)
    ap: Arc[Pet] = Arc.new(Dog())
    arc_handle(Arc.new(Point(2)), ap)
    w = rc.downgrade()
    weak_handle(w)
    weak_subclass(make_weak_sub(rc))
    arc_payload(ap)
    q2 = ap.get()
    if isinstance(q2, Dog):
        print("arc_payload: through ap", q2.n)
    get_payload(rc)
    get_payload(rc)
    get_payload(Rc.new(Cat()))
    deref_payload(rc)
    Kennel(Box(Dog())).show()
    q = rc.get()
    if isinstance(q, Dog):
        print("deref: through rc", q.n)
    deref_record(bp)
    print("deref_record: through bp", bp.get().x)
    match_get(b)
    match_deref(b)
    match_deref(Box(Cat()))
    print(box_get(b))
    print(box_get(Box(Cat())))
    shadow_alias(b)
    shadow_alias(Box(Cat()))
    print(rebind_handle())
    match_local(b)
    match_call_mutates(Holder(Box(Dog())))
    optional_box(None)
    optional_box(b)
    upgrade_method(Hound())
    user_deref(Handle(Point(3)))
    pt = Point(4)
    ptr_object(pt)


main()
