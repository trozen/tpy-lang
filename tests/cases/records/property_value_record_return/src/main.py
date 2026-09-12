# Regression: a @property returning a value-type record (bare or Optional)
# emitted its mutable auto_readonly clone alongside the const one -- two
# identical const C++ signatures -> redefinition error. The prune ran in
# register_record, before the protocol pass sets user records' ValueType
# flags; it now runs with the deferred value-type validation pass.
from typing import Optional
from tpy import int32, ValueType


class Coord(ValueType):
    x: int32
    y: int32

    def __init__(self, x: int, y: int) -> None:
        self.x = x
        self.y = y

    def __eq__(self, other: "Coord") -> bool:
        return self.x == other.x and self.y == other.y


class Track:
    _cx: int32
    _cy: int32
    _has_goal: bool

    def __init__(self, cx: int, cy: int) -> None:
        self._cx = cx
        self._cy = cy
        self._has_goal = False

    @property
    def position(self) -> Coord:  # bare value-record return
        return Coord(int(self._cx), int(self._cy))

    @property
    def goal(self) -> Optional[Coord]:  # Optional value-record return
        if not self._has_goal:
            return None
        return Coord(0, 0)


def main() -> None:
    t = Track(3, 4)
    p = t.position
    print(p.x, p.y)
    print(t.position == Coord(3, 4))
    g = t.goal
    print(g is None)


main()
