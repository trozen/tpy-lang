# A cycle peer's enum is complete while the other members register: field
# defaults, value-record fields and header-held bodies of a_user read the
# enums of z_peer, which sorts later. Imported in the order CPython accepts.
import a_user
import z_peer
from a_user import TD


def retarget(d: a_user.DC) -> None:
    d.e = z_peer.E.A


def main() -> None:
    # dataclass field default
    d = a_user.DC(9)
    print("dataclass", d.n, d.e.value)
    retarget(d)  # the record crosses a call; the caller sees the write
    print("dataclass retargeted", d.e.value)
    # identity and kwargs construction
    k = a_user.DC(n=1, e=z_peer.E.A)
    print("identity", d.e is z_peer.E.A, k.e is z_peer.E.A, k.e is z_peer.E.B)
    # plain-class field default
    p = a_user.PC(4)
    print("plain", p.e.value, p.n)
    # value record
    v = a_user.VR(3, z_peer.E.B)
    print("value record", v.n, v.e.value)
    # TypedDict (the default is ignored, every field is passed)
    t = TD(n=2, e=z_peer.E.A)
    print("typeddict", t["n"], t["e"].value)
    # Optional field
    o = a_user.OP()
    print("optional", o.o is not None, o.o == z_peer.E.A)
    # int8 IntEnum, negative values
    lv = a_user.LV()
    print("int8 enum", lv.lv.value, lv.lv.name, z_peer.Level.HIGH.value)
    # parameter default
    print("param default", a_user.param_default(), a_user.param_default(z_peer.E.A), z_peer.peer_call())
    # match arm
    print("match", a_user.match_arm(z_peer.E.A), a_user.match_arm(z_peer.E.B))
    # header-held body
    a_user.show(5)
    # nested types
    print("nested", z_peer.nested())
    # enum method called at run time on a field default
    tn = a_user.TN()
    print("companion", tn.tone.label(), z_peer.Tone.SOFT.label())
    # @dynamic protocol beside the peer's enums
    leaf = z_peer.Leaf()
    print("dynamic", z_peer.tag_of(leaf))


main()
