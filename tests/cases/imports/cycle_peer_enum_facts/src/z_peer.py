# Cycle peer sorting after a_user: defines the enums a_user's declarations
# read, plus a nested enum and a nested class no peer uses.
import enum
from typing import Protocol
import a_user
from tpy import int8, int32, dynamic


class E(enum.Enum):
    A = 1
    B = 2


# an int8-backed enum with negative, non-sequential values
class Level(int8, enum.Enum):
    LOW = -5
    MID = 3
    HIGH = 40


# an enum with a method: its companion record rides on the enum's facts
class Tone(enum.Enum):
    SOFT = 1
    LOUD = 2

    def label(self) -> str:
        return "<" + self.name + ">"


# a @dynamic protocol beside the top-level enums of a cycle member
@dynamic
class Named(Protocol):
    def tag(self) -> str: ...


class Leaf(Named):
    def __init__(self) -> None:
        pass

    def tag(self) -> str:
        return "leaf"


def tag_of(n: Named) -> str:
    return n.tag()


# nested types in a cycle member: the forward header declares top-level ones only
class Outer:
    class Kind(enum.Enum):
        X = 7

    class Inner:
        def __init__(self, k: int32) -> None:
            self.k = k



def nested() -> int32:
    return Outer.Inner(Outer.Kind.X.value).k


# a call from the peer defining the enums into the member that names them
def peer_call() -> int32:
    return a_user.param_default()
