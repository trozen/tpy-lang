"""Generate match programs across the tier-selection matrix.

The AST picks one of several match lowerings from the subject's type and the
arm shapes (primitive switch, enum switch, string switch, variant-index,
dynamic-cast chain, if/elif), and each lowering raises on a pattern kind it
does not handle. Which kinds those are is what nobody had established, so this
crosses every tier with every pattern kind rather than guessing which crossings
are interesting.

Generated rather than committed: the inputs are a cross product, so a generator
is smaller than the corpus AND is the only form in which "every crossing" is
checkable rather than asserted.
"""
from __future__ import annotations

from pathlib import Path

ENUM_PRE = """from enum import Enum


class Color(Enum):
    RED = 1
    GREEN = 2
    BLUE = 3
    CYAN = 4
"""

REC_PRE = """class A:
    x: int

    def __init__(self, x: int) -> None:
        self.x = x


class B:
    x: int

    def __init__(self, x: int) -> None:
        self.x = x


class C:
    x: int

    def __init__(self, x: int) -> None:
        self.x = x
"""

DYN_PRE = """from typing import Protocol
from tpy import dynamic


@dynamic
class Shape(Protocol):
    def area(self) -> int: ...


class Sq(Shape):
    s: int

    def __init__(self, s: int) -> None:
        self.s = s

    def area(self) -> int:
        return self.s * self.s


class Tri(Shape):
    b: int

    def __init__(self, b: int) -> None:
        self.b = b

    def area(self) -> int:
        return self.b
"""

# EVERY subject carries EVERY preamble, so an arm naming `A`, `Color` or `Sq`
# resolves whatever the subject is. A subject carrying only its own preamble
# refuses the crossing as an undefined NAME before the tier/pattern question is
# asked, and a name error counts as sema gating the pattern class while probing
# nothing -- the failure is silent because the bucket looks the same either way.
_ALL_PRE = "from tpy import Int32\n" + ENUM_PRE + "\n" + REC_PRE + "\n" + DYN_PRE

# (name, preamble, param type, call argument)
SUBJECTS = [
    ("int", _ALL_PRE, "Int32", "Int32(1)"),
    ("bool", _ALL_PRE, "bool", "True"),
    ("str", _ALL_PRE, "str", '"a"'),
    ("float", _ALL_PRE, "float", "1.5"),
    ("enum", _ALL_PRE, "Color", "Color.RED"),
    ("union", _ALL_PRE, "A | B", "A(1)"),
    ("optrec", _ALL_PRE, "A | None", "A(1)"),
    ("optval", _ALL_PRE, "Int32 | None", "Int32(1)"),
    ("record", _ALL_PRE, "A", "A(1)"),
    ("dyn", _ALL_PRE, "Shape", "Sq(2)"),
]

# Every pattern kind the parser produces, so a tier's unhandled kinds surface
# rather than being guessed at.
ARMS = [
    ("lit_int", "1"),
    ("lit_str", '"a"'),
    ("lit_bool", "True"),
    ("lit_float", "1.5"),
    ("lit_none", "None"),
    ("or_lit", "1 | 2"),
    ("or_lit_mixed", '1 | "a"'),
    ("capture", "n"),
    ("wildcard_first", "_"),
    ("as_lit", "1 as n"),
    ("as_cls_a", "A() as n"),
    ("cls_a", "A()"),
    ("cls_a_field_lit", "A(x=1)"),
    ("cls_a_field_cap", "A(x=n)"),
    ("cls_a_field_none", "A(x=None)"),
    ("or_cls", "A() | B()"),
    ("or_cls_as", "A() | B() as n"),
    ("enum_member", "Color.RED"),
    ("or_enum", "Color.RED | Color.GREEN"),
    ("as_enum", "Color.RED as n"),
    ("cls_sq", "Sq()"),
    ("or_dyn", "Sq() | Tri()"),
    ("cls_sq_field", "Sq(s=2)"),
    ("as_dyn", "Sq() as n"),
]

# Only the STRING tier is arm-count gated (`_should_switch_str` counts str-literal
# arms, or-pattern alternatives included, against STRING_SWITCH_THRESHOLD), so it
# is the only family that needs several arms to be entered at all. The primitive
# and enum switches are chosen from the subject TYPE, and a union subject routes
# on its arms (a guard, a shared variant index or a field-value sub-pattern sends
# it to the guarded render instead) -- all three are already reached by the
# single-arm round. What extra arms buy the rest is POSITION: an odd arm before,
# among and after a literal run.
# Every family takes `_ALL_PRE` for the same reason SUBJECTS does -- a class
# pattern is the only odd-arm kind that reaches the switch tiers' "Unsupported
# pattern in <kind> switch" raise, and it must resolve to reach it.
# (name, preamble, param type, arg, tier-selecting arms, odd arms)
MULTI_FAMILIES = [
    ("intsw", _ALL_PRE, "Int32", "Int32(1)",
     ["1", "2", "3", "4"],
     ["n", '"a"', "1.5", "None", "True", "1 | 2", "1 as n", "_", "A()"]),
    # Six arms against STRING_SWITCH_THRESHOLD = 5
    # (codegen_cpp/string_dispatch.py). Below the threshold this family probes
    # the if/elif tier while looking like it probes the string switch, which is
    # the failure worth guarding: it is silent, since both tiers emit. Six
    # rather than five is only headroom -- splicing never lowers the count.
    ("strsw", _ALL_PRE, "str", '"a"',
     ['"a"', '"b"', '"c"', '"d"', '"e"', '"f"'],
     ["s", "1", "1.5", "None", '"a" | "b"', '"a" as s', "_", "A()"]),
    ("enumsw", _ALL_PRE, "Color", "Color.RED",
     ["Color.RED", "Color.GREEN", "Color.BLUE", "Color.CYAN"],
     ["c", "1", "None", "Color.RED | Color.GREEN", "Color.RED as c", "_",
      "A()"]),
    ("unionsw", _ALL_PRE, "A | B | C", "A(1)",
     ["A()", "B()", "C()"],
     ["u", "None", "A() | B()", "A() as a", "A(x=1)", "A(x=n)", "1", "_"]),
]

_TEMPLATE = """{pre}

def f(v: {ptype}) -> int:
    match v:
{body}        case _:
            return -1


def main() -> None:
    print(f({arg}))


main()
"""


def _render(pre: str, ptype: str, arg: str, arms: list[str],
            guard_idx: int) -> str:
    body = ""
    for i, a in enumerate(arms):
        g = " if True" if guard_idx == i else ""
        body += f"        case {a}{g}:\n            return {i}\n"
    return _TEMPLATE.format(pre=pre, ptype=ptype, body=body, arg=arg)


def _write(out: Path, name: str, text: str) -> None:
    d = out / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "main.py").write_text(text)


def gen_single(out: Path) -> int:
    """One odd arm plus a wildcard, over every subject kind, with and without
    a guard."""
    n = 0
    for sname, pre, ptype, arg in SUBJECTS:
        for aname, arm in ARMS:
            for guard in (False, True):
                name = f"{sname}__{aname}{'__g' if guard else ''}"
                _write(out, name,
                       _render(pre, ptype, arg, [arm], 0 if guard else -1))
                n += 1
    return n


def gen_multi(out: Path) -> int:
    """Enough homogeneous arms to select a switch tier, with one odd arm
    spliced at the front, middle and end of the literal run.

    The odd arm is never the LAST arm of the match: `_TEMPLATE` always appends
    a wildcard, so the "unsupported TRAILING pattern" raises are out of this
    matrix's reach and only the interior ones are in it. Dropping the wildcard
    to reach them would make most families non-exhaustive and refused in sema,
    so it needs a different construction rather than a flag here.
    """
    n = 0
    for fam, pre, ptype, arg, base, odds in MULTI_FAMILIES:
        for oi, odd in enumerate(odds):
            for pos, label in ((0, "first"), (len(base) // 2, "mid"),
                               (len(base), "last")):
                arms = base[:pos] + [odd] + base[pos:]
                for guard in (-1, pos):
                    name = (f"{fam}__odd{oi}__{label}"
                            f"{'__g' if guard >= 0 else ''}")
                    _write(out, name, _render(pre, ptype, arg, arms, guard))
                    n += 1
    return n
