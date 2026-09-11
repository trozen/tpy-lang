# A checked container ELEMENT read (`things[0]`, `d["a"]`) as the argument at
# a record BORROW slot of a constructor (a local decl, a field write inside
# and outside `__init__`, a nested constructor argument, a slot the callee
# mutates), a user method, a `@staticmethod` (standing in for every
# marker-qualified call kind) and a builtin stub (`count` / `index`): the
# element lvalue binds the slot inline, the render the free call already had.
# Callees that MUTATE through the slot write into the list element, so a
# silent copy would show. An `Own[record]` slot keeps rejecting
# (error_record_elem_subscript_own_ctor_slot). The stub section appends
# first: a literal-seeded list demoted to an Array has no `count` / `index`
# render (BUGS.md#demoted-array-list-count-index). The closure, with, try,
# match, async and @error_return positions are not sectioned: the argument
# sinks are position-independent, and the generator and module-level
# sections cover the two positions with a different variable model.
from __future__ import annotations
from typing import Iterator
from tpy import Int32, Own


class Thing:
    x: float

    def __init__(self, x: Int32) -> None:
        self.x = float(x)

    def __eq__(self, o: Thing) -> bool:
        return self.x == o.x


class Player:
    x: float

    def __init__(self, thing: Thing) -> None:
        self.x = thing.x


# Mutates through the borrowed slot.
class Bump:
    x: float

    def __init__(self, thing: Thing) -> None:
        thing.x += 10.0
        self.x = thing.x


class Wrap:
    b: Bump

    def __init__(self, b: Own[Bump]) -> None:
        self.b = b


class Bag:
    total: float

    def __init__(self) -> None:
        self.total = 0.0

    def take(self, t: Thing) -> None:
        t.x += 1.0
        self.total += t.x

    @staticmethod
    def peek(t: Thing) -> float:
        t.x += 100.0
        return t.x


class Map:
    things: list[Thing]
    player: Player
    bumped: Bump

    # The doom shape: a field write in the constructor body, off a field
    # container a method filled; the second write mutates the element.
    def __init__(self, n: Int32) -> None:
        self.things = []
        self.fill(n)
        self.player = Player(self.things[0])  # tpyc: ok
        self.bumped = Bump(self.things[0])  # tpyc: ok

    def fill(self, n: Int32) -> None:
        for i in range(n):
            self.things.append(Thing(i + 4))

    # Method body, field write, mutating the element.
    def reseat(self) -> None:
        self.bumped = Bump(self.things[1])  # tpyc: ok


# Constructor slots: a local decl, a nested constructor argument, a mutated slot.
def ctor_slots() -> None:
    things = [Thing(4), Thing(5)]
    p = Player(things[0])  # tpyc: ok
    p.x += 1.0
    print("ctor_local_decl", p.x, things[0].x)
    w = Wrap(Bump(things[1]))  # tpyc: ok
    print("ctor_nested", w.b.x, things[1].x)
    b = Bump(things[0])  # tpyc: ok
    print("ctor_mutated_slot", b.x, things[0].x)


# User method and static method slots.
def method_slots() -> None:
    things = [Thing(4), Thing(5)]
    bag = Bag()
    bag.take(things[0])  # tpyc: ok
    print("method", bag.total, things[0].x)
    v = Bag.peek(things[1])  # tpyc: ok
    print("static_method", v, things[1].x)


# Builtin stub slots.
def stub_slots() -> None:
    things = [Thing(4), Thing(5)]
    things.append(Thing(4))
    print("stub_count", things.count(things[0]))  # tpyc: ok
    print("stub_index", things.index(things[1]))  # tpyc: ok


# A dict element.
def dict_elem() -> None:
    d = {"a": Thing(7)}
    b = Bump(d["a"])  # tpyc: ok
    print("dict_elem", b.x, d["a"].x)


# Generator body.
def gen(things: list[Thing]) -> Iterator[float]:
    for i in range(len(things)):
        b = Bump(things[i])  # tpyc: ok
        yield b.x


def main() -> None:
    m = Map(2)
    print("ctor_field_write", m.player.x, m.bumped.x, m.things[0].x)
    m.reseat()
    print("method_field_write", m.bumped.x, m.things[1].x)
    ctor_slots()
    method_slots()
    stub_slots()
    dict_elem()
    things = [Thing(1), Thing(2)]
    for v in gen(things):
        print("generator", v)
    print("generator", things[0].x, things[1].x)


main()

# Module-level statement.
top = [Thing(1)]
tb = Bump(top[0])  # tpyc: ok
print("module_level", tb.x, top[0].x)
