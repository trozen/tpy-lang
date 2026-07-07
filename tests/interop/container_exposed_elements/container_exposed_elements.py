# tpy: ext_module
# Exposed enums and classes as CONTAINER elements at the @export boundary:
# list[Color], list[Counter], dict[str, Counter], set[Color], dict[Color, V],
# and an enum tuple element. Each element marshals through its module type handle
# (copy-in/out). Enum elements preserve the singleton; class elements copy (the
# container cliff -- a mutated class-element param is not visible to the caller;
# see ext_checks). (An exposed CLASS as a tuple element, and any exposed type
# nested inside a container element, are deferred -- see the error_export_* cases.)
from tpy import Int64, Own
from enum import IntEnum
from tpy.extern import export


@export
class Color(IntEnum):
    RED = 1
    GREEN = 2
    BLUE = 3


@export
class Counter:
    def __init__(self, v: Int64):
        self.value = v

    def bump(self) -> None:
        self.value += 1


@export
def cycle(cs: list[Color]) -> Own[list[Color]]:
    out: list[Color] = []
    for c in cs:
        out.append(Color.GREEN if c == Color.RED else Color.RED)
    return out


@export
def total(cs: list[Counter]) -> Int64:
    t: Int64 = 0
    for c in cs:
        t += c.value
    return t


@export
def dict_total(m: dict[str, Counter]) -> Int64:
    t: Int64 = 0
    for k in m:
        t += m[k].value
    return t


@export
def counters(n: Int64) -> Own[list[Counter]]:
    out: list[Counter] = []
    i = 0
    while i < n:
        out.append(Counter(i))
        i += 1
    return out


@export
def bump_all(cs: list[Counter]) -> None:
    for c in cs:
        c.bump()


@export
def dedup(cs: list[Color]) -> Own[set[Color]]:
    out: set[Color] = set()
    for c in cs:
        out.add(c)
    return out


@export
def weight(m: dict[Color, Int64], c: Color) -> Int64:
    return m[c]


@export
def tag_value(t: tuple[Color, Int64]) -> Int64:
    return t[1] if t[0] == Color.RED else -t[1]
