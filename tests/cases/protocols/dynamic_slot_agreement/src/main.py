# In the @dynamic protocols a class combines a method name is one slot;
# filled with its receiver form it keeps dispatching through a base-typed caller.
from typing import Protocol, Self, overload
from tpy import dynamic, readonly, Own, String, ValueType, int32


class Ray:
    d: float

    def __init__(self, d: float) -> None:
        self.d = d


# Section plain: one mutable form fills one mutable slot.
@dynamic
class Mirror(Protocol):
    def reflect(self, ray: Ray) -> float: ...


class Flat(Mirror):
    def __init__(self) -> None:
        pass

    def reflect(self, ray: Ray) -> float:  # tpyc: ok
        return ray.d


class Bent(Flat):
    # The write is the subject: it must reach the caller's Ray.
    def reflect(self, ray: Ray) -> float:  # tpyc: ok
        ray.d += 1.0
        return ray.d


def plain_trace(m: Flat, r: Ray) -> float:
    return m.reflect(r)


# Section inferred: a plain override of a @readonly slot is inferred readonly.
@dynamic
class Lens(Protocol):
    @readonly
    def focus(self, ray: readonly[Ray]) -> float: ...


class Convex(Lens):
    k: float

    def __init__(self, k: float) -> None:
        self.k = k

    @readonly
    def focus(self, ray: readonly[Ray]) -> float:  # tpyc: ok
        return ray.d + self.k


class Concave(Convex):
    def focus(self, ray: readonly[Ray]) -> float:  # tpyc: ok
        return ray.d - self.k


def inferred_trace(m: readonly[Convex], r: Ray) -> float:
    return m.focus(r)


# Section static_parent: a @readonly redeclaration of a plain protocol
# parent's method is the one virtual.
class Shape(Protocol):
    def area(self) -> int32: ...


@dynamic
class ReadShape(Shape, Protocol):
    @readonly
    def area(self) -> int32: ...


class Square(ReadShape):
    k: int32

    def __init__(self) -> None:
        self.k = 1

    def grow(self) -> None:
        self.k += 1

    @readonly
    def area(self) -> int32:  # tpyc: ok
        return self.k


class Big(Square):
    @readonly
    def area(self) -> int32:  # tpyc: ok
        return self.k * 10


def static_parent_area(s: Square) -> int32:
    s.grow()
    return s.area()


# Section generic_parent: a @readonly redeclaration of a generic plain
# protocol parent's method is the one virtual.
class Source[T](Protocol):
    def read(self) -> T: ...


@dynamic
class ReadSource(Source[int32], Protocol):
    @readonly
    def read(self) -> int32: ...


class Tank(ReadSource):
    level: int32

    def __init__(self) -> None:
        self.level = 3

    def fill(self) -> None:
        self.level += 4

    @readonly
    def read(self) -> int32:  # tpyc: ok
        return self.level


def generic_parent_read(s: ReadSource) -> int32:
    return s.read()


# Section same_form_redecl: a child @dynamic protocol redeclaring a @dynamic
# parent's slot with the same receiver form inherits that one virtual.
@dynamic
class Pump(Protocol):
    def push(self, ray: Ray) -> float: ...


@dynamic
class LoudPump(Pump, Protocol):
    def push(self, ray: Ray) -> float: ...


class Piston(LoudPump):
    def __init__(self) -> None:
        pass

    def push(self, ray: Ray) -> float:  # tpyc: ok
        ray.d *= 2.0
        return ray.d


def same_form_redecl_push(p: Pump, r: Ray) -> float:
    return p.push(r)


# Section identical_parents: two @dynamic parents declaring the same slot
# give the protocol combining them one slot, reached through each of them.
@dynamic
class Pusher(Protocol):
    def push(self, ray: Ray) -> float: ...


@dynamic
class Shover(Protocol):
    def push(self, ray: Ray) -> float: ...


@dynamic
class Mover(Pusher, Shover, Protocol):
    pass


class Cart:
    def __init__(self) -> None:
        pass

    def push(self, ray: Ray) -> float:  # tpyc: ok
        ray.d += 1.0
        return ray.d


class Wagon(Mover):
    def __init__(self) -> None:
        pass

    def push(self, ray: Ray) -> float:  # tpyc: ok
        ray.d += 10.0
        return ray.d


def via_pusher(p: Pusher, r: Ray) -> float:
    return p.push(r)


def via_shover(s: Shover, r: Ray) -> float:
    return s.push(r)


def identical_parents_trace(m: Mover, r: Ray) -> str:
    # The call through Mover names its one combined slot.
    a = m.push(r)  # tpyc: ok
    b = via_pusher(m, r)
    c = via_shover(m, r)
    return f"{a} {b} {c}"


# Section own_scalar_slots: `-> Own[int32]` and `-> int32` are one slot.
@dynamic
class Ticker(Protocol):
    def ticks(self) -> int32: ...


@dynamic
class OwnTicker(Protocol):
    def ticks(self) -> Own[int32]: ...


class Metronome(Ticker, OwnTicker):
    n: int32

    def __init__(self) -> None:
        self.n = 0

    def ticks(self) -> Own[int32]:  # tpyc: ok
        self.n += 1
        return self.n


def own_scalar_a(t: Ticker) -> int32:
    return t.ticks()


def own_scalar_b(t: OwnTicker) -> int32:
    return t.ticks()


# Section structural_readonly_fills_mutable: a structural conformer's
# @readonly method fills a mutable slot.
@dynamic
class Probe(Protocol):
    def sample(self) -> int32: ...


class Sensor:
    level: int32

    def __init__(self) -> None:
        self.level = 5

    @readonly
    def sample(self) -> int32:
        return self.level


def structural_readonly_trace() -> str:
    s = Sensor()
    p: Probe = s
    first = p.sample()  # tpyc: ok
    # The protocol local aliases s: the write is seen through it.
    s.level = 9
    return f"{first} {p.sample()}"


# Section generic_parent_record: a generic class's slot, taken at its
# subclass's type argument, is the one slot a concrete protocol declares too.
@dynamic
class Gauge[T](Protocol):
    def gauge(self) -> T: ...


@dynamic
class IntGauge(Protocol):
    def gauge(self) -> int32: ...


class Dial[T](Gauge[T]):
    n: int32
    v: T

    def __init__(self, v: T) -> None:
        self.n = 0
        # An open T may be a reference type, which this store copies.
        self.v = v  # tpyc: warning(/may copy T into field/)

    def gauge(self) -> T:
        return self.v


class TickDial(Dial[int32], IntGauge):
    def __init__(self) -> None:
        super().__init__(0)

    def gauge(self) -> int32:  # tpyc: ok
        self.n += 1
        return self.n


def via_gauge(g: Gauge[int32]) -> int32:
    return g.gauge()


def via_int_gauge(g: IntGauge) -> int32:
    return g.gauge()


# Section own_bounded_record: `-> T` and `-> Own[T]` are one slot at a
# generic class implementing both, whose `T: ValueType` makes T a value type.
@dynamic
class Peek[T](Protocol):
    def peek(self) -> T: ...


@dynamic
class OwnPeek[T](Protocol):
    def peek(self) -> Own[T]: ...


class Cell[T: ValueType](Peek[T], OwnPeek[T]):
    x: T
    reads: int32

    def __init__(self, x: T) -> None:
        self.x = x
        self.reads = 0

    def peek(self) -> Own[T]:  # tpyc: ok
        self.reads += 1
        return self.x


def via_peek(p: Peek[int32]) -> int32:
    return p.peek()


def via_own_peek(p: OwnPeek[int32]) -> int32:
    return p.peek()


# Section structural_readonly_converting: a structural @readonly method whose
# return converts to the slot's fills a @readonly slot.
@dynamic
class Label(Protocol):
    @readonly
    def text(self) -> str: ...


class Tag:
    s: str

    def __init__(self) -> None:
        self.s = "a"

    @readonly
    def text(self) -> String:
        return String(self.s)


def structural_readonly_converting_trace() -> str:
    t = Tag()
    lab: Label = t
    first = lab.text()  # tpyc: ok
    # The protocol local aliases t: the write is seen through it.
    t.s = "b"
    return f"{first} {lab.text()}"


# Section own_stub: an `Own[Self]` @overload stub specializes the plain-self
# implementation, which fills the plain slot.
@dynamic
class Counter(Protocol):
    def tick(self) -> int32: ...


class Clock(Counter):
    n: int32

    def __init__(self) -> None:
        self.n = 0

    @overload
    def tick(self: Own[Self]) -> int32: ...
    def tick(self) -> int32:  # tpyc: ok
        self.n += 1
        return self.n


def own_stub_tick(c: Counter) -> int32:
    return c.tick()


# Section own_value_combiner: `-> Own[str]` and `-> str` are one slot in
# either parent order.
@dynamic
class OwnName(Protocol):
    def name(self) -> Own[str]: ...


@dynamic
class PlainName(Protocol):
    def name(self) -> str: ...


@dynamic
class OwnFirst(OwnName, PlainName, Protocol):  # tpyc: ok
    pass


@dynamic
class PlainFirst(PlainName, OwnName, Protocol):  # tpyc: ok
    pass


class Named(OwnFirst):
    n: int32

    def __init__(self) -> None:
        self.n = 0

    def name(self) -> Own[str]:
        self.n += 1
        return "x" + str(self.n)


class Titled(PlainFirst):
    n: int32

    def __init__(self) -> None:
        self.n = 0

    def name(self) -> Own[str]:
        self.n += 1
        return "y" + str(self.n)


def via_own_name(p: OwnName) -> str:
    return p.name()


def via_plain_name(p: PlainName) -> str:
    return p.name()


def via_own_first(p: OwnFirst) -> str:
    return p.name()


def via_plain_first(p: PlainFirst) -> str:
    return p.name()


# Section own_result_bound: a protocol method's `Own[str]` result bound to
# a local is the caller's, through a @dynamic and a type-parameter receiver.
class OwnLabel(Protocol):
    def name(self) -> Own[str]: ...


def bound_own_name(p: OwnName) -> str:
    s = p.name()  # tpyc: ok
    return s + "!"


def bound_own_label[T: OwnLabel](p: T) -> str:
    s = p.name()  # tpyc: ok
    return s + "?"


# Section plain_bridge_agree: agreeing @dynamic slots reach a @dynamic
# protocol through plain bridge protocols, one slot per name.
@dynamic
class Steer(Protocol):
    def turn(self, x: int32) -> int32: ...


@dynamic
class Helm(Protocol):
    def turn(self, x: int32) -> int32: ...


class LeftBridge(Steer, Protocol):
    pass


class RightBridge(Helm, Protocol):
    pass


class Bridges(LeftBridge, RightBridge, Protocol):
    def trim(self) -> int32: ...


@dynamic
class Rudder(Bridges, Protocol):
    def keel(self) -> int32: ...


# Boat is not passed to via_steer / via_helm: a class whose @dynamic protocol
# reaches Steer / Helm only through plain protocols cannot yet be passed as
# them (BUGS.md#dynamic-ancestor-through-plain-protocol-not-a-base).
class Boat(Rudder):
    n: int32

    def __init__(self) -> None:
        self.n = 0

    def turn(self, x: int32) -> int32:
        self.n += x
        return self.n

    def trim(self) -> int32:
        return 5

    def keel(self) -> int32:
        return self.n * 10


class Raft:
    n: int32

    def __init__(self) -> None:
        self.n = 100

    def turn(self, x: int32) -> int32:
        self.n += x
        return self.n


def via_steer(s: Steer) -> int32:
    return s.turn(1)


def via_helm(h: Helm) -> int32:
    return h.turn(2)


def via_rudder(r: Rudder) -> int32:
    # The one turn slot, reached through both plain bridges.
    t = r.turn(3)  # tpyc: ok
    return t + r.trim() + r.keel()


def via_bridges[T: Bridges](b: T) -> int32:
    return b.turn(4) + b.trim()


# Section keyword_order: a child @dynamic protocol redeclaring an inherited
# slot with its parameters in another order binds keywords by its own names.
@dynamic
class Mixer(Protocol):
    def mix(self, hi: int32, lo: int32) -> int32: ...


@dynamic
class SwapMixer(Mixer, Protocol):
    def mix(self, lo: int32, hi: int32) -> int32: ...  # tpyc: ok


class Blender(SwapMixer):
    n: int32

    def __init__(self) -> None:
        self.n = 0

    def mix(self, lo: int32, hi: int32) -> int32:
        self.n += 1
        return 10 * hi + lo


def via_swap_mixer(m: SwapMixer) -> int32:
    # The keyword call is the subject: `hi` and `lo` reach Blender by name.
    return m.mix(hi=2, lo=1)


def via_swap_bound[T: SwapMixer](m: T) -> int32:
    return m.mix(hi=3, lo=1)


def via_mixer(m: Mixer) -> int32:
    # Positional through the parent: a keyword there binds Mixer's names
    # (BUGS.md#protocol-keyword-binds-protocol-names).
    return m.mix(4, 1)


# Section redecl_own_value: a child @dynamic protocol redeclaring `-> str`
# as `-> Own[str]` names the one inherited slot.
@dynamic
class Caption(Protocol):
    def text(self) -> str: ...


@dynamic
class OwnCaption(Caption, Protocol):
    def text(self) -> Own[str]: ...  # tpyc: ok


class Banner(OwnCaption):
    n: int32

    def __init__(self) -> None:
        self.n = 0

    def text(self) -> Own[str]:
        self.n += 1
        return "b" + str(self.n)


class Sign:
    def text(self) -> Own[str]:
        return "s"


def via_caption(c: Caption) -> str:
    return c.text()


def via_own_caption(c: OwnCaption) -> str:
    return c.text()


def main() -> None:
    p = Ray(2.0)
    print("plain:", plain_trace(Flat(), p), plain_trace(Bent(), p), p.d)
    q = Ray(2.0)
    print("inferred:", inferred_trace(Convex(1.0), q), inferred_trace(Concave(1.0), q))
    b = Big()
    # The grow() through the parameter is seen here: no copy at the call.
    print("static_parent:", static_parent_area(b), b.k)
    t = Tank()
    t.fill()
    print("generic_parent:", generic_parent_read(t))
    s = Ray(1.5)
    # The write through the Pump slot reaches this Ray.
    print("same_form_redecl:", same_form_redecl_push(Piston(), s), s.d)
    u = Ray(0.0)
    cart = Cart()
    # Each push through Mover, Pusher and Shover writes this Ray.
    print("identical_parents:", identical_parents_trace(cart, u), u.d)
    w = Ray(0.0)
    print("identical_parents:", identical_parents_trace(Wagon(), w), w.d)
    mt = Metronome()
    # Both slots tick this Metronome.
    print("own_scalar_slots:", own_scalar_a(mt), own_scalar_b(mt), mt.n)
    print("structural_readonly_fills_mutable:", structural_readonly_trace())
    d = TickDial()
    # Both slots tick this TickDial.
    print("generic_parent_record:", via_int_gauge(d), via_gauge(d), d.n)
    c = Cell(7)
    # Both slots count their reads on this Cell.
    print("own_bounded_record:", via_peek(c), via_own_peek(c), c.reads)
    print("structural_readonly_converting:", structural_readonly_converting_trace())
    k = Clock()
    # The tick through the protocol writes this Clock.
    print("own_stub:", own_stub_tick(k), own_stub_tick(k), k.n)
    nm = Named()
    # Each call through either parent order counts on this Named.
    print("own_value_combiner:", via_own_first(nm), via_own_name(nm), via_plain_name(nm), nm.n)
    tt = Titled()
    print("own_value_combiner:", via_plain_first(tt), via_own_name(tt), via_plain_name(tt), tt.n)
    print("own_result_bound:", bound_own_name(tt), bound_own_label(tt), tt.n)
    bt = Boat()
    # Each turn through Rudder and the plain bridges moves this Boat.
    print("plain_bridge_agree:", via_rudder(bt), via_bridges(bt), bt.n)
    rf = Raft()
    # A structural conformer turns through both @dynamic slots.
    print("plain_bridge_agree:", via_steer(rf), via_helm(rf), rf.n)
    bl = Blender()
    # Each mix through SwapMixer and Mixer counts on this Blender.
    print("keyword_order:", via_swap_mixer(bl), via_swap_bound(bl), via_mixer(bl), bl.n)
    bn = Banner()
    # Each text through Caption and OwnCaption counts on this Banner.
    print("redecl_own_value:", via_caption(bn), via_own_caption(bn), bn.n)
    print("redecl_own_value:", via_own_caption(Sign()), via_caption(Sign()))


main()
