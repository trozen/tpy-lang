# Enum members as record field defaults: zero-arg construction uses the
# in-class initializer (Color tint = Color::RED;), explicit defaults too,
# and a cross-module enum default qualifies correctly.
from enum import Enum
from tpy import Int32

from sidemod import Remote, RemoteState


class Color(Enum):
    RED = 0
    GREEN = 1
    BLUE = 2


class Shape:
    tint: Color = Color.RED
    count: Int32 = 0


class Tagged:
    kind: Color = Color.BLUE

    def __init__(self) -> None:
        pass


class Pinned:
    # cross-module enum default: must qualify to sidemod's namespace
    state: RemoteState = RemoteState.BUSY


def main() -> None:
    s = Shape()
    print(1 if s.tint == Color.RED else 0)
    s.tint = Color.GREEN
    print(1 if s.tint == Color.GREEN else 0)
    t = Tagged()
    print(1 if t.kind == Color.BLUE else 0)
    r = Remote()
    print(1 if r.state == RemoteState.IDLE else 0)
    p = Pinned()
    print(1 if p.state == RemoteState.BUSY else 0)


main()
