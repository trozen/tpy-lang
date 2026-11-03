# Native modules are no C++ cycle peers: an import cycle through a native
# module keeps only its regular members as C++ peers (none when one is left).
# Sections native_enum (a cycle member's @native enum, plain enum and record,
# named by its peer) and native_enum_dep (a @native enum declared by an
# imported native module's includes): a member's _fwd.hpp carries them.
from nat_a import LIMIT, Inner, peek
from nat_b import Outer
from holder import Holder, Garage, Turbo, Msg
from engine import Engine
from left import Left
from right import Right
from hub import Hub
from owner import RelaySlot, relay_seven, bump
import gauge
import sig
import tint
import tint_peer

def native_pair() -> None:
    o = Outer()
    print("native_pair:", LIMIT, o.held.v, peek(o))

def regular_native() -> None:
    e = Engine()
    h = Holder(e)
    # The holder aliases the engine: a write after binding shows through it.
    e.bias = 10
    print("regular_native:", h.run())
    e2 = Engine()
    h.rebind(e2)
    e2.bias = 1
    print("regular_native:", h.run())

def regular_native_field() -> None:
    g = Garage()
    # A write through the by-value field shows in the holder's own read.
    g.engine.bias = 3
    print("regular_native_field:", g.run())

def regular_native_base() -> None:
    t = Turbo()
    m = Msg()
    print("regular_native_base:", t.take(m), t.boost())

def through_native() -> None:
    hub = Hub()
    left = Left(hub)
    right = Right(hub)
    hub.bias = 100
    print("through_native:", left.run(), right.run())

def self_reexport() -> None:
    s = RelaySlot(3)
    bump(s)
    print("self_reexport:", s.v, relay_seven())

def native_enum() -> None:
    g = gauge.Gauge(3)
    gauge.show("native_enum:", g)
    r = sig.Reading(6)
    # The record is borrowed: a write after construction shows in the read.
    r.v = 8
    print("native_enum:", g.n, sig.gauge_n(), gauge.reading_v(r))

def native_enum_dep() -> None:
    print("native_enum_dep:", tint.pick(), tint.Col.RED, tint_peer.paint())

def main() -> None:
    native_pair()
    regular_native()
    regular_native_field()
    regular_native_base()
    through_native()
    self_reexport()
    native_enum()
    native_enum_dep()

main()
