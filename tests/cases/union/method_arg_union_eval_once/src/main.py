# A call with a value-union param, nested as a METHOD-call argument, must
# evaluate that inner call exactly ONCE (regression: the method-arg codegen
# path emitted the union-temp hoist twice, so the arg's side effects ran
# twice -- an evaluate-once divergence from CPython). Each "eval <tag>" must
# print once. Covers the user-method-arg form (the bug), the free-function
# form (was always single -- the inverse), a direct union method arg, and a
# cpp_template-backed method (list.append -- the other regenerating branch).
from dataclasses import dataclass
from tpy import ValueType, int32


@dataclass(frozen=True)
class Fixed(ValueType):
    off: int32


@dataclass(frozen=True)
class Zone(ValueType):
    zid: int32


class Box:
    v: int32

    def __init__(self, tz: Fixed | Zone | None = None) -> None:
        if tz is None:
            self.v = 0
        elif isinstance(tz, Fixed):
            self.v = int(tz.off)
        else:
            self.v = int(tz.zid)

    def eat(self, other: "Box") -> int:
        return int(other.v)


def free_eat(other: Box) -> int:
    return int(other.v)


def mk(tag: str, n: int) -> Fixed:
    print("eval " + tag)
    return Fixed(n)


def main() -> None:
    b = Box()
    # METHOD-call argument whose inner ctor takes a value-union param: the
    # inner mk() side effect must fire once.
    print(b.eat(Box(mk("method", 3))))
    # FREE-function argument: the path that was already single.
    print(free_eat(Box(mk("free", 4))))
    # Direct union arg to a method (Box param is the union): once.
    print(b.eat(Box(mk("direct", 5))))
    # Zone arm through the same method-arg shape: once.
    print(b.eat(Box(Zone(9))))
    # cpp_template-backed method (list.append): the other regenerating
    # branch. The Box(mk(...)) element's inner mk() must fire once.
    xs: list[Box] = []
    xs.append(Box(mk("append", 8)))
    print(len(xs), int(xs[0].v))


main()
