# A member named like a local type shadows that type in C++ record scope, so
# codegen qualifies the colliding reference. Covers every C++-scope member kind
# (method, field, property, class constant, MRO-inherited method + field, static
# method, param type) against a record, enum, and @dynamic protocol; `boxed()`
# moves a @nocopy Box across the accessor boundary and mutates it (no copy).
from enum import Enum
from typing import Final, Protocol
from tpy import Own, Int32, dynamic
from tplib import Box


class day:
    n: int
    def __init__(self, n: int) -> None:
        self.n = n


class Color(Enum):
    RED = 1
    GREEN = 2


@dynamic
class shape(Protocol):
    def area(self) -> int: ...


class square:
    side: int
    def __init__(self, side: int) -> None:
        self.side = side
    def area(self) -> int:
        return self.side * self.side


class clock:
    tick: int
    def __init__(self, tick: int) -> None:
        self.tick = tick

    def day(self) -> Own[day]:              # method shadows record type `day`
        return day(self.tick)


class widget:
    Color: int                              # field shadows enum type `Color`
    def __init__(self, c: int) -> None:
        self.Color = c
    def kind(self) -> Color:
        return Color.RED if self.Color == 0 else Color.GREEN


class gauge:
    day: Final[Int32] = 3                   # class constant shadows type `day`
    def read(self) -> Own[day]:
        return day(9)


class base:
    def day(self) -> int:                   # base method `day`...
        return 1


class sub(base):
    def build(self) -> Own[day]:            # ...inherited into sub, shadows `day`
        return day(self.day())


class factory:
    @staticmethod
    def day() -> Own[day]:                  # static method shadows type `day`
        return day(7)
    def relabel(self, day: day) -> int:     # param typed `day` (also the shadow)
        return day.n


class canvas:
    def shape(self) -> int:                 # method shadows protocol type `shape`
        return 1
    def draw(self, s: shape) -> int:        # param typed protocol `shape`
        return s.area()


class meter:
    def build(self) -> Own[day]:            # references type `day`...
        return day(6)
    @property
    def day(self) -> int:                   # ...property `day` shadows it
        return 6


class fbase:
    day: int                                # base field `day`
    def __init__(self, d: int) -> None:
        self.day = d


class fsub(fbase):
    def __init__(self, d: int) -> None:
        fbase.__init__(self, d)
    def build(self) -> Own[day]:            # inherited field `day` shadows type
        return day(self.day)


class printer:
    # Inverse guard: no member named `day`, so its `day` reference must render
    # BARE (not qualified) -- confirms qualification stays scoped to colliding
    # records. The snapshot is the check.
    def emit(self) -> Own[day]:
        return day(2)


class holder:
    day: int                                # field `day`; accessor moves a Box out
    def __init__(self, day: int) -> None:
        self.day = day
    def boxed(self) -> Own[Box[day]]:
        return Box(day(self.day))


def main() -> None:
    print(clock(5).day().n)
    print(widget(0).kind() == Color.RED)
    print(gauge().read().n)
    print(sub().build().n)
    print(factory.day().n, factory().relabel(day(4)))
    print(canvas().shape(), canvas().draw(square(4)))
    print(meter().build().n, meter().day)
    print(fsub(8).build().n)
    print(printer().emit().n)
    b = holder(7).boxed()
    b.get().n = 99                          # mutate through the moved Box (no copy)
    print(b.get().n)


main()
