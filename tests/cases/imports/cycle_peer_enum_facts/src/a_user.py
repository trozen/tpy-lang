# Import-cycle member whose declarations read the enums of z_peer, a peer
# that registers later: field defaults, field types, a template body.
from dataclasses import dataclass
from typing import TypedDict
from tpy import int32, ValueType
import z_peer


# dataclass field default
@dataclass
class DC:
    n: int32
    e: z_peer.E = z_peer.E.B  # a later peer's member as a field default  # tpyc: ok


# plain-class field default
class PC:
    e: z_peer.E = z_peer.E.B  # the same default on a plain class  # tpyc: ok

    def __init__(self, n: int32) -> None:
        self.n = n


# value record with a peer-enum field
class VR(ValueType):
    n: int32
    e: z_peer.E  # the peer enum is a value type for the value-field check  # tpyc: ok

    def __init__(self, n: int32, e: z_peer.E) -> None:
        self.n = n
        self.e = e


# TypedDict field default
class TD(TypedDict):
    n: int32
    e: z_peer.E = z_peer.E.B  # validated as a member, then ignored as in CPython  # tpyc: warning(/default value.*ignored/)


# Optional field defaulting to a member
class OP:
    o: z_peer.E | None = z_peer.E.A  # a member defaulting an Optional field  # tpyc: ok

    def __init__(self) -> None:
        pass


# IntEnum with an int8 underlying type and negative values
class LV:
    lv: z_peer.Level = z_peer.Level.LOW  # the peer's underlying type and values  # tpyc: ok

    def __init__(self) -> None:
        pass


# enum with a method (companion record) as a field default
class TN:
    tone: z_peer.Tone = z_peer.Tone.LOUD  # a peer enum that carries a companion  # tpyc: ok

    def __init__(self) -> None:
        pass


# parameter default naming the peer member
def param_default(e: z_peer.E = z_peer.E.B) -> int32:  # filled at the call  # tpyc: ok
    return e.value


# match arm on the peer enum
def match_arm(e: z_peer.E) -> str:
    match e:
        case z_peer.E.A:  # a peer member as a value pattern  # tpyc: ok
            return "a"
        case z_peer.E.B:
            return "b"
    return "?"


# printing the peer enum in a header-held (template) body
def show[T](x: T) -> None:
    print("template", x, z_peer.E.B, z_peer.E.B.name)  # needs the peer's operator<< and EnumUtil  # tpyc: ok
