#!/usr/bin/env python3
"""The argument-source x parameter-slot x CALLEE-FAMILY verdict ratchet.

Whether an argument expression is admitted at a parameter slot is decided by
`tpyc/thir/lower/arg_table.py`, and every callee family carries its OWN
ordered row list (`_PLAIN_ARG_SINK`, `_GENERIC_PLAIN_ARG_SINK`,
`_METHOD_ARG_SINK`, `_CTOR_ARG_SINK`, `_CTOR_NESTED_ARG_SINK`,
`_PROTOCOL_ARG_SINK`). A row present in one
family and absent in another means the same argument at the same-shaped slot
compiles for one kind of callee and is refused for another. Some of those
differences are stated rules; the rest is drift. Nothing else in the tree
measures which is which, because the corpus pins the cells someone thought to
write.

This script generates the matrix, surveys it, and writes the verdict table
next to itself. Same kind of instrument as `property_position_sweep.py` beside
it: a committed measurement, so a change that moves a cell shows up as a diff
in `arg_family_sweep.expected.json` instead of as a review discovery.

ONE CELL PER FUNCTION, NOT PER PROGRAM. A cell is (source shape) x (parameter
slot, in a non-mutating or a MUTATING callee body) x (callee family), and the
subject of a cell is ONE caller function. `collect_thir(tolerate_reject=True)`
is a survey -- every function body is attempted and a reject is recorded per
body -- so hundreds of cells share one compilation and one implicit-stdlib
pass. The whole table is ~70 compilations instead of ~5700 processes.

WHAT STILL COSTS A COMPILATION is a SEMA error: sema stops the module at its
first one, so each sema cell is found, attributed to the function whose line
range holds it, dropped, and the program recompiled. That is why the generic
column -- which carries most of the unresolvable-`T` cells -- is quarantined
in its own program per slot: one column's inference failure must not force a
recompile of the other seven.

WHAT A CELL RECORDS. Three strings: the VERDICT (`ok`, `reject:<tag>` or
`sema:<message>`), the first WARNING inside the cell's own function or its
family's fixture, and -- for an admitted cell -- the normalised THIR of the
subject statement plus anything the call hoisted ahead of it. The THIR render
is the half a verdict column cannot show: two families that both admit while
one materialises an argument temp and the other passes the expression in place
differ silently.

WHAT IT DOES NOT RECORD. Behaviour. Nothing is built or run, so a cell that
copies where CPython aliases reads exactly like one that binds -- the render
column narrows that gap but does not close it. Copy-vs-alias stays the corpus
cases' job.

NOT AN ARM PIN. The house rule is that a THIR lowering arm is pinned by a case
under `tests/cases/`; this is a measurement over a generated corpus, like
`container_gates.py` and `inventory_sites.py` in the same directory.

ONE COLUMN IS NOT THE SINK ITS NAME SUGGESTS, measured rather than
assumed (see `--routes`); the `static` column reaches the plain free-call
family, as every receiver-less spelling does:

  * `nested` -- a constructor whose argument is itself a constructor argument
    (`Outer(Inner(x))`) routes to the DIRECT ctor family. The restricted
    `_CTOR_NESTED_ARG_SINK` serves only flush-LESS nested positions, which are
    reached through a handful of argument-temp recursions rather than through
    any callee spelling; the chain this column uses (`Rc.new(Box(K(x)))`, the
    `Own[@dynamic P]` conformer recursion) is the shortest one that lands
    there.

  python scripts/thir_migration/review/arg_family_sweep.py            # check
  python scripts/thir_migration/review/arg_family_sweep.py --update   # rewrite
  python scripts/thir_migration/review/arg_family_sweep.py --only str # a slice
  python scripts/thir_migration/review/arg_family_sweep.py --routes   # which
                                                 # sink each column reaches
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path
from typing import NamedTuple

# The tree the sweep compiles against. `--repo` retargets it, so the script
# runs on a checkout other than the one it lives in.
REPO = Path(__file__).resolve().parents[3]

EXPECTED = Path(__file__).with_suffix(".expected.json")

# Quote and brace characters the cell templates need INSIDE string literals.
# Spelling them inline would close the surrounding literal or collide with the
# `@NAME@` substitution scan, and naming them keeps each row one line.
Q = chr(34)
LB = chr(123)
RB = chr(125)

# Printed immediately before the call in every cell, so the lowered body can be
# cut at the subject statement without having to parse the render back.
BEACON = 424242


class Kind(NamedTuple):
    """One argument VALUE TYPE, with the spellings the source shapes need.

    A slot names a kind; the sources ask the kind how to produce a value of
    it. `None` for a spelling means the shape does not exist for this kind and
    every source needing it is skipped.
    """
    ty: str                 # the storage-form annotation
    seed: str               # an owned rvalue of `ty`
    obs: str                # an expression reading `@X@`, for the trailing use
    fieldable: bool = True  # can be a record field / property / method return
    listable: bool = True   # `list[ty]` is constructible
    dictable: bool = True   # `dict[str, ty]` is constructible
    lit: str | None = None      # a non-empty literal of `ty`
    empty: str | None = None    # the empty literal of `ty`
    comp: str | None = None     # a comprehension of `ty`
    ctor: str | None = None     # a constructor rvalue of `ty`
    slc: str | None = None      # a slice of `@X@` yielding `ty`
    view: bool = False          # has a view-returning getter flavour
    fstr: bool = False          # an f-string is a value of `ty`
    sub: str | None = None      # a constructor rvalue of a SUBCLASS of `ty`
    imports: tuple[str, ...] = ()   # what the kind's spellings import
    defs: tuple[str, ...] = ()      # the kind's own type definitions


# The subclass of `R` the upcast rows are measured with.
SUB_DEFS = ("class Sub(R):", "    def __init__(self, x: int32) -> None:",
            "        super().__init__(x)", "", "")

# The user-`Deref` wrappers: a record whose members are reached through
# `__deref__`, over a record and over a container payload -- the two
# instantiations the hop's gates are asked about.
DEREF_REC_DEFS = ("class DR(Deref[R]):", "    _inner: R", "",
                  "    def __init__(self, x: int32) -> None:",
                  "        self._inner = R(x)", "",
                  "    def __deref__(self) -> R:", "        return self._inner",
                  "", "")
DEREF_LIST_DEFS = ("class DL(Deref[list[int32]]):", "    _inner: list[int32]",
                   "", "    def __init__(self) -> None:",
                   "        self._inner = [1, 2]", "",
                   "    def __deref__(self) -> list[int32]:",
                   "        return self._inner", "", "")

# The recursive-union WRAPPER: on the reference axis by value form, but a
# struct whose payload is a member, so a gate that admits it as a record or a
# container binds the wrong thing. Its cells are a tripwire, not a target.
WRAPPER_DEFS = ("type Json = None | bool | int32 | str | list[Json]", "", "",
                "def mk_json() -> Own[Json]:", "    j: Json = [1, 2]",
                "    return j", "", "")


KINDS: dict[str, Kind] = {
    "rec": Kind(ty="R", seed="R(1)", obs="@X@.x", ctor="R(7)", sub="Sub(5)",
                defs=SUB_DEFS),
    # A GENERIC record with a record type-arg: the gates that still read the
    # record slice ask the type-arg spelling fence of this kind.
    "gen_rec": Kind(ty="Box[R]", seed="Box(R(1))", obs="@X@.get().x",
                    ctor="Box(R(7))", dictable=False,
                    imports=("from tplib import Box",)),
    "deref_rec": Kind(ty="DR", seed="DR(1)", obs="@X@.x", ctor="DR(7)",
                      defs=DEREF_REC_DEFS),
    "deref_list": Kind(ty="DL", seed="DL()", obs="len(deref(@X@))",
                       ctor="DL()", defs=DEREF_LIST_DEFS),
    "array_i": Kind(ty="Array[int32, 2]", seed="[1, 2]", obs="len(@X@)",
                    lit="[3, 4]", listable=False, dictable=False),
    "ru_wrap": Kind(ty="Json", seed="mk_json()", obs="1", fieldable=False,
                    listable=False, dictable=False, defs=WRAPPER_DEFS),
    "opt_rec": Kind(ty="Optional[R]", seed="R(2)",
                    obs="0 if @X@ is None else @X@.x",
                    listable=False, dictable=False, ctor="R(8)"),
    "list_i": Kind(ty="list[int32]", seed="[1, 2]", obs="len(@X@)",
                   lit="[3, 4]", empty="[]",
                   comp="[i for i in range(2)]", slc="@X@[0:1]"),
    "list_r": Kind(ty="list[R]", seed="[R(1)]", obs="len(@X@)",
                   listable=False, lit="[R(5)]", empty="[]",
                   comp="[R(i) for i in range(2)]", slc="@X@[0:1]"),
    "dict_si": Kind(ty="dict[str, int32]", seed=LB + Q + "a" + Q + ": 1" + RB,
                    obs="len(@X@)", dictable=False,
                    lit=LB + Q + "b" + Q + ": 2" + RB, empty=LB + RB,
                    comp=LB + "k: 1 for k in [" + Q + "c" + Q + "]" + RB),
    "set_i": Kind(ty="set[int32]", seed=LB + "1, 2" + RB, obs="len(@X@)",
                  lit=LB + "3, 4" + RB,
                  comp=LB + "i for i in range(2)" + RB),
    "str": Kind(ty="str", seed=Q + "abc" + Q, obs="len(@X@)",
                lit=Q + "lit" + Q, empty=Q + Q, slc="@X@[0:2]",
                view=True, fstr=True),
    "bytes": Kind(ty="bytes", seed="b" + Q + "ab" + Q, obs="len(@X@)",
                  lit="b" + Q + "xy" + Q, empty="b" + Q + Q, slc="@X@[0:1]"),
    "bytearray": Kind(ty="bytearray", seed="bytearray(b" + Q + "ab" + Q + ")",
                      obs="len(@X@)",
                      ctor="bytearray(b" + Q + "cd" + Q + ")",
                      empty="bytearray()", slc="@X@[0:1]"),
    "i32": Kind(ty="int32", seed="3", obs="@X@", lit="7"),
    "bigint": Kind(ty="int", seed="10", obs="@X@", lit="12"),
    "tup_is": Kind(ty="tuple[int32, str]", seed="(1, " + Q + "a" + Q + ")",
                   obs="@X@[0]", lit="(2, " + Q + "b" + Q + ")"),
    "tup_ri": Kind(ty="tuple[R, int32]", seed="(R(1), 2)", obs="@X@[1]",
                   lit="(R(3), 4)"),
    "uni_rec": Kind(ty="R | Other", seed="R(1)", obs="1", ctor="R(6)"),
    "uni_vals": Kind(ty="int32 | str", seed="3", obs="1", lit="5"),
    "proto_p": Kind(ty="Impl2", seed="Impl2()", obs="@X@.ping()",
                    ctor="Impl2()"),
    "callable": Kind(ty="Callable[[int32], int32]", seed="fn_id",
                     obs="@X@(1)", listable=False, dictable=False,
                     lit="lambda q: q"),
}


class Slot(NamedTuple):
    """One parameter slot: its annotation, the kind a source must produce for
    it, and the write a MUTATING callee body makes through it (`None` where a
    write is not expressible, so the slot has only the read half)."""
    name: str
    ann: str
    kind: str
    mut: tuple[str, ...] = ()
    # An OPEN type-parameter slot exists only where the callee can declare
    # one, so the non-generic columns report `n/a` rather than a verdict.
    generic_only: bool = False


SLOTS: tuple[Slot, ...] = (
    Slot("rec", "R", "rec", ("p.x += 1",)),
    Slot("rec_ro", "readonly[R]", "rec"),
    Slot("rec_own", "Own[R]", "rec", ("p.x += 1",)),
    Slot("rec_opt", "Optional[R]", "opt_rec"),
    Slot("gen_rec", "Box[R]", "gen_rec", ("p.get().x += 1",)),
    Slot("deref_rec", "DR", "deref_rec", ("p.x = 9",)),
    Slot("deref_list", "DL", "deref_list", ("p.append(9)",)),
    Slot("array_i", "Array[int32, 2]", "array_i", ("p[0] = 9",)),
    Slot("ru_wrap", "Json", "ru_wrap"),
    Slot("list_i", "list[int32]", "list_i", ("p.append(9)",)),
    Slot("list_i_ro", "readonly[list[int32]]", "list_i"),
    Slot("list_i_own", "Own[list[int32]]", "list_i", ("p.append(9)",)),
    Slot("list_r", "list[R]", "list_r", ("p.append(R(9))",)),
    Slot("dict_si", "dict[str, int32]", "dict_si",
         ("p[" + Q + "z" + Q + "] = 9",)),
    Slot("set_i", "set[int32]", "set_i", ("p.add(9)",)),
    Slot("str", "str", "str"),
    Slot("str_own", "Own[str]", "str"),
    Slot("strview", "StrView", "str"),
    Slot("bytes", "bytes", "bytes"),
    Slot("bytearray", "bytearray", "bytearray", ("p.append(9)",)),
    Slot("i32", "int32", "i32"),
    Slot("bigint", "int", "bigint"),
    Slot("tup_is", "tuple[int32, str]", "tup_is"),
    Slot("tup_ri", "tuple[R, int32]", "tup_ri"),
    Slot("uni_rec", "R | Other", "uni_rec"),
    Slot("uni_vals", "int32 | str", "uni_vals"),
    Slot("proto_p", "P2", "proto_p"),
    Slot("callable", "Callable[[int32], int32]", "callable"),
    Slot("span_i", "Span[int32]", "list_i", ("p[0] = 9",)),
    Slot("open_t", "T", "i32", generic_only=True),
)


class Source(NamedTuple):
    """One argument EXPRESSION shape.

    `need` names the kind spelling the shape consumes, so a shape is skipped
    where the kind cannot produce it. `pre` / `post` are body lines at the
    call's own indent; `wrap` lines open a construct (a loop, an `if`, a
    nested `def`) and push the call one level deeper.
    """
    name: str
    expr: str
    need: str = ""
    defs: tuple[str, ...] = ()
    pre: tuple[str, ...] = ()
    wrap: tuple[str, ...] = ()
    post: tuple[str, ...] = ()
    param: bool = False
    param_ty: str = "@TY@"      # the parameter's annotation when `param`


# `@SEED@` / `@TY@` / `@LIT@` / `@EMPTY@` / `@COMP@` / `@CTOR@` / `@SLICE@`
# come from the kind; `@OBS_x@` reads the local `x`.
SOURCES: tuple[Source, ...] = (
    # A local read AGAIN after the call: never a move candidate.
    Source("local", "s0", pre=("s0 = @SEED@",), post=("print(@OBS_s0@)",)),
    # ... and the same local at its LAST use, which is.
    Source("local_last", "s0", pre=("s0 = @SEED@",)),
    # A SUBCLASS instance at the base-typed slot: the upcast rows.
    Source("subclass_local", "s0", need="sub", pre=("s0 = @SUB@",),
           post=("print(@OBS_s0@)",)),
    Source("param", "pv", param=True),
    # An Optional PARAMETER proven non-None: the pointer-repr borrow binding
    # (`T*`), where the narrowed local above is a value-repr slot.
    Source("narrowed_opt_param", "pv", need="field", param=True,
           param_ty="Optional[@TY@]", wrap=("if pv is not None:",)),
    Source("obj_field", "b._v", need="field", pre=("b = B()",)),
    Source("nested_field", "w.inner._v", need="field", pre=("w = W()",)),
    Source("subscript", "xs[0]", need="list", pre=("xs = [@SEED@]",)),
    Source("dict_subscript", "d[" + Q + "k" + Q + "]", need="dict",
           pre=("d = " + LB + Q + "k" + Q + ": @SEED@" + RB,)),
    Source("borrow_method", "b.v_m()", need="field", pre=("b = B()",)),
    Source("prop_ref", "b.v", need="field", pre=("b = B()",)),
    Source("prop_own", "b.v_own", need="field", pre=("b = B()",)),
    Source("prop_opt", "b.v_opt", need="field", pre=("b = B()",)),
    Source("prop_view", "b.v_view", need="view", pre=("b = B()",)),
    Source("own_call", "mk()", need="field"),
    Source("ctor_rvalue", "@CTOR@", need="ctor"),
    Source("temp_accessor", "mk_b().v", need="field"),
    # The FIELD of a call rvalue, beside the property above: a field read has
    # its own receiver gate.
    Source("temp_field", "mk_b()._v", need="field"),
    Source("literal", "@LIT@", need="lit"),
    Source("empty_literal", "@EMPTY@", need="empty"),
    Source("comprehension", "@COMP@", need="comp"),
    Source("ternary_names", "s0 if cond() else s1",
           pre=("s0 = @SEED@", "s1 = @SEED@")),
    Source("ternary_calls", "mk() if cond() else mk()", need="field"),
    # A narrowed Optional local: the name is the OPTIONAL's, proven non-None.
    Source("narrowed_opt", "o0", need="field",
           pre=("o0: Optional[@TY@] = @SEED@",),
           wrap=("if o0 is not None:",)),
    Source("none_lit", "None"),
    Source("fstring", "f" + Q + "v" + LB + "s0" + RB + Q, need="fstr",
           pre=("s0 = @SEED@",)),
    Source("slice_expr", "@SLICE_s0@", need="slice", pre=("s0 = @SEED@",)),
    Source("copy_call", "copy(s0)", pre=("s0 = @SEED@",),
           post=("print(@OBS_s0@)",)),
    Source("global_name", "G", need="field"),
    Source("loop_var", "s0", need="list",
           pre=("xs = [@SEED@, @SEED@]",), wrap=("for s0 in xs:",)),
    Source("dict_items_value", "s0", need="dict",
           pre=("d = " + LB + Q + "k" + Q + ": @SEED@" + RB,),
           wrap=("for dk0, s0 in d.items():",)),
    # A name captured by a nested def, the call inside it.
    Source("closure_capture", "s0", pre=("s0 = @SEED@",),
           wrap=("def inner() -> None:",), post=("inner()",)),
)


class Family(NamedTuple):
    """One CALLEE family: the fixture that declares it, and the call that
    reaches it. Every family's callee body is the same text, so a verdict
    difference between two columns is a difference between their argument
    tables.

    All the columns now share one program, so the callee names have to be
    distinct. The ones a SEMA message can name -- the free / generic function,
    the constructor's record, the protocol and its conformer -- keep the
    spelling they had one-cell-per-program, because the message is part of
    the verdict. The method names, which no message spells, are made distinct
    per column so no column's record accidentally conforms to another's
    protocol.
    """
    name: str
    defs: tuple[str, ...]
    call: tuple[str, ...]
    head: tuple[str, ...] = ()      # cell-function preamble (receiver, ...)
    params: tuple[str, ...] = ()    # extra cell-function parameters
    args: tuple[str, ...] = ()      # ... and what the cell's driver passes
    imports: tuple[str, ...] = ()
    open_t: bool = False            # may carry an open type-parameter slot


FAMILIES: tuple[Family, ...] = (
    Family("fn",
           defs=("def take(p: @SLOT@) -> None:", "    @BODY@", ""),
           call=("take(@ARG@)",)),
    Family("gen",
           defs=("def take[T](x: T, p: @SLOT@) -> None:", "    print(x)",
                 "    @BODY@", ""),
           call=("take(1, @ARG@)",), open_t=True),
    Family("meth",
           defs=("class Taker:", "    tag: int32", "",
                 "    def __init__(self) -> None:", "        self.tag = 0",
                 "", "    def take_meth(self, p: @SLOT@) -> None:",
                 "        @BODY@", ""),
           head=("t = Taker()",), call=("t.take_meth(@ARG@)",)),
    Family("static",
           defs=("class Holder:", "    @staticmethod",
                 "    def take_static(p: @SLOT@) -> None:", "        @BODY@",
                 ""),
           call=("Holder.take_static(@ARG@)",)),
    Family("ctor",
           defs=("class Take:", "    tag: int32", "",
                 "    def __init__(self, p: @SLOT@) -> None:",
                 "        self.tag = 0", "        @BODY@", ""),
           call=("k0 = Take(@ARG@)", "print(k0.tag)")),
    # The flush-LESS nested ctor position. `Outer(Inner(x))` is NOT one (its
    # temps flush at the enclosing statement, so it gates as a direct ctor);
    # the `Own[@dynamic P]` conformer recursion inside a second owning
    # constructor is the shortest chain that reaches the restricted family.
    Family("nested",
           imports=("from tplib import Box, Rc",),
           defs=("@dynamic", "class DynP(Protocol):",
                 "    def ping(self) -> int32: ...", "", "",
                 "class KNested:", "    tag: int32", "",
                 "    def __init__(self, p: @SLOT@) -> None:",
                 "        self.tag = 0", "        @BODY@", "",
                 "    def ping(self) -> int32:", "        return self.tag",
                 ""),
           call=("rc: Rc[Box[DynP]] = Rc.new(Box(KNested(@ARG@)))",
                 "print(rc.get().get().ping())")),
    Family("proto",
           defs=("class Reader(Protocol):",
                 "    def take_proto(self, p: @SLOT@) -> None: ...", "", "",
                 "class Impl:", "    tag: int32", "",
                 "    def __init__(self) -> None:", "        self.tag = 0",
                 "", "    def take_proto(self, p: @SLOT@) -> None:",
                 "        @BODY@", ""),
           params=("pp: Reader",), args=("Impl()",),
           call=("pp.take_proto(@ARG@)",)),
    Family("genrec",
           # The type parameter arrives as `Own[T]`, so the record's own
           # constructor does not add a copy warning to every cell in this
           # column and bury the cell's own diagnostic.
           defs=("class GR[T]:", "    t: T", "",
                 "    def __init__(self, t: Own[T]) -> None:",
                 "        self.t = t",
                 "", "    def take_genrec(self, p: @SLOT@) -> None:",
                 "        @BODY@", ""),
           head=("g = GR(1)",), call=("g.take_genrec(@ARG@)",), open_t=True),
)

FAMILY_BY_NAME = {f.name: f for f in FAMILIES}

# The generic column carries most of the unresolvable-`T` cells, and a sema
# error costs a whole recompilation of the program it lands in. Quarantining
# it keeps those recompilations off the other seven columns.
GROUPS: dict[str, tuple[str, ...]] = {
    "gen": ("gen",),
    "main": ("fn", "meth", "static", "ctor", "nested", "proto", "genrec"),
}

BASE_IMPORTS = (
    "from typing import Callable, Optional, Protocol",
    "from tpy import (Array, Deref, Own, Span, StrView, copy, deref, dynamic,"
    " int32, readonly)",
)

# The records every cell shares. `Other` and `P2`/`Impl2` are spliced in only
# where a slot names them, so an unrelated program does not carry their
# diagnostics.
BASE_DEFS = (
    "class R:",
    "    x: int32",
    "",
    "    def __init__(self, x: int32) -> None:",
    "        self.x = x",
    "",
    "",
    "def cond() -> bool:",
    "    return True",
    "",
    "",
    "def fn_id(n: int32) -> int32:",
    "    return n",
    "",
)

OTHER_DEFS = ("class Other:", "    y: int32", "",
              "    def __init__(self, y: int32) -> None:", "        self.y = y",
              "", "")

PROTO_DEFS = ("class P2(Protocol):", "    def ping(self) -> int32: ...", "",
              "", "class Impl2:", "    n: int32", "",
              "    def __init__(self) -> None:", "        self.n = 1", "",
              "    def ping(self) -> int32:", "        return self.n", "")

# The FIXTURE record: one field of the program's kind plus a getter per
# flavour the property sweep distinguishes, so the field / property /
# borrow-method / temporary-receiver sources all read the same storage.
FIXTURE = (
    "class B:",
    "    _v: @TY@",
    "",
    "    def __init__(self) -> None:",
    "        self._v = @SEED@",
    "",
    "    @property",
    "    def v(self) -> @TY@:",
    "        return self._v",
    "",
    "    @property",
    "    def v_own(self) -> Own[@TY@]:",
    "        return @SEED@",
    "",
    "    @property",
    "    def v_opt(self) -> Optional[@TY@]:",
    "        return self._v",
    "",
    "    def v_m(self) -> @TY@:",
    "        return self._v",
    "",
    "",
    "class W:",
    "    inner: B",
    "",
    "    def __init__(self) -> None:",
    "        self.inner = B()",
    "",
    "",
    "def mk() -> Own[@TY@]:",
    "    return @SEED@",
    "",
    "",
    "def mk_b() -> Own[B]:",
    "    return B()",
    "",
    "",
    "G: @TY@ = @SEED@",
    "",
)

VIEW_GETTER = ("    @property", "    def v_view(self) -> StrView:",
               "        return self._v", "")


def _kind_has(kind: Kind, need: str) -> bool:
    return {
        "": True,
        "field": kind.fieldable,
        "list": kind.listable,
        "dict": kind.dictable,
        "lit": kind.lit is not None,
        "empty": kind.empty is not None,
        "comp": kind.comp is not None,
        "ctor": kind.ctor is not None,
        "slice": kind.slc is not None,
        "view": kind.view,
        "fstr": kind.fstr,
        "sub": kind.sub is not None,
    }[need]


def _subst(text: str, kind: Kind) -> str:
    out = text.replace("@TY@", kind.ty).replace("@SEED@", kind.seed)
    out = out.replace("@LIT@", kind.lit or "").replace("@EMPTY@",
                                                       kind.empty or "")
    out = out.replace("@COMP@", kind.comp or "")
    out = out.replace("@CTOR@", kind.ctor or "")
    out = out.replace("@SUB@", kind.sub or "")
    for name in ("s0", "s1"):
        out = out.replace("@OBS_" + name + "@", kind.obs.replace("@X@", name))
        out = out.replace("@SLICE_" + name + "@",
                          (kind.slc or "").replace("@X@", name))
    return out


# ---------------------------------------------------------------------------
# The matrix.
# ---------------------------------------------------------------------------

def programs() -> list[tuple[str, Slot, bool, str]]:
    """Every (program name, slot, mutating, group)."""
    out = []
    for slot in SLOTS:
        for mut in (False, True):
            if mut and not slot.mut:
                continue
            for group in sorted(GROUPS):
                if slot.generic_only and not any(
                        FAMILY_BY_NAME[f].open_t for f in GROUPS[group]):
                    continue
                out.append(("%s__%s__%s" % (slot.name, "mut" if mut else "ro",
                                            group), slot, mut, group))
    return out


def program_cells(slot: Slot, mut: bool, group: str) -> list[tuple[str, str]]:
    """The (source, family) cells a program carries, in emission order.

    The free-function column comes first within each source: a row it answers
    with a sema error is type-invalid for every column, and finding that first
    lets the whole row be dropped in one recompilation instead of eight.
    """
    kind = KINDS[slot.kind]
    fams = [f for f in GROUPS[group]
            if not slot.generic_only or FAMILY_BY_NAME[f].open_t]
    order = [f.name for f in FAMILIES]
    fams.sort(key=order.index)
    return [(src.name, fam) for src in SOURCES
            if _kind_has(kind, src.need) for fam in fams]


def cell_name(slot: Slot, mut: bool, source: str, family: str) -> str:
    return "%s__%s__%s__%s" % (slot.name, "mut" if mut else "ro", source,
                               family)


def all_cells() -> dict[str, str]:
    """Every cell the matrix defines, as {cell name: program name}.

    Generation only -- nothing is compiled -- so the gate can check that the
    committed table and the generator still describe the same matrix without
    paying for a run.
    """
    out: dict[str, str] = {}
    for name, slot, mut, group in programs():
        for source, family in program_cells(slot, mut, group):
            out[cell_name(slot, mut, source, family)] = name
    return out


def program_of(cell: str) -> str:
    """The program a committed cell name belongs to."""
    slot, half, _source, family = cell.rsplit("__", 3)
    return "%s__%s__%s" % (slot, half,
                           "gen" if family == "gen" else "main")


class Built(NamedTuple):
    """A program's text plus the line ranges a verdict is attributed by."""
    text: str
    cell_lines: dict[tuple[str, str], tuple[int, int]]
    family_lines: dict[str, tuple[int, int]]


def build_program(slot: Slot, mut: bool, group: str,
                  cells: list[tuple[str, str]]) -> Built:
    """One program: the shared fixture, one callee fixture per family in the
    group, and one caller function per cell."""
    kind = KINDS[slot.kind]
    body = list(slot.mut) if mut else ["pass"]
    fams = [FAMILY_BY_NAME[f] for f in GROUPS[group]]
    fams = [f for f in fams if not slot.generic_only or f.open_t]
    used = {fam for _src, fam in cells}

    lines: list[str] = list(BASE_IMPORTS)
    # Only what the cells present actually need: a one-cell program built for
    # a sema check must not drag in a column's library imports.
    for fam in fams:
        if fam.name not in used:
            continue
        lines += [i for i in fam.imports if i not in lines]
    lines += [i for i in kind.imports if i not in lines]
    lines += [""]
    lines += list(BASE_DEFS) + [""]
    lines += list(kind.defs)
    if slot.kind == "uni_rec":
        lines += list(OTHER_DEFS)
    if slot.kind == "proto_p":
        lines += list(PROTO_DEFS)
    if kind.fieldable:
        fixture = list(FIXTURE)
        if kind.view:
            fixture = fixture[:fixture.index("    def v_m(self) -> @TY@:")]
            fixture += list(VIEW_GETTER)
            fixture += ["    def v_m(self) -> @TY@:", "        return self._v",
                        "", ""]
            fixture += FIXTURE[FIXTURE.index("class W:"):]
        lines += [_subst(x, kind) for x in fixture]

    family_lines: dict[str, tuple[int, int]] = {}
    for fam in fams:
        if fam.name not in used:
            continue
        start = len(lines) + 1
        for line in fam.defs:
            if "@BODY@" in line:
                pad = line[:len(line) - len(line.lstrip())]
                lines += [pad + b for b in body]
            else:
                lines.append(line.replace("@SLOT@", slot.ann))
        family_lines[fam.name] = (start, len(lines))

    cell_lines: dict[tuple[str, str], tuple[int, int]] = {}
    by_name = {s.name: s for s in SOURCES}
    for source, family in cells:
        src, fam = by_name[source], FAMILY_BY_NAME[family]
        start = len(lines) + 1
        params = list(fam.params) + (
            ["pv: " + _subst(src.param_ty, kind)] if src.param else [])
        lines += ["def cell_%s__%s(%s) -> None:"
                  % (source, family, ", ".join(params))]
        lines += ["    " + h for h in fam.head]
        lines += ["    " + _subst(p, kind) for p in src.pre]
        depth = 1 + len(src.wrap)
        for i, w in enumerate(src.wrap):
            lines += ["    " * (1 + i) + _subst(w, kind)]
        arg = _subst(src.expr, kind)
        lines += ["    " * depth + "print(" + str(BEACON) + ")"]
        lines += ["    " * depth + c.replace("@ARG@", arg) for c in fam.call]
        lines += ["    " + _subst(p, kind) for p in src.post]
        # The driver, for the cells whose PARAMETERS carry a check of their
        # own: a protocol-typed parameter has its conformance verdict only
        # where a conformer is passed, and a value parameter its type check.
        # Kept inside a function rather than at module level so the whole
        # program stays surveyable.
        call_args = list(fam.args) + ([kind.seed] if src.param else [])
        if call_args:
            lines += ["", "", "def drive_%s__%s() -> None:" % (source, family),
                      "    cell_%s__%s(%s)" % (source, family,
                                               ", ".join(call_args))]
        lines += ["", ""]
        cell_lines[(source, family)] = (start, len(lines))
    return Built("\n".join(lines) + "\n", cell_lines, family_lines)


def generate(out_dir: Path, only: str = "") -> dict[str, Path]:
    """Write every program in full; return {program name: path}.

    The survey rewrites a program in place while it drops sema cells, so what
    is left on disk afterwards is the last subset it compiled. The full text
    is what a reader wants, which is why it is written here first.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    out: dict[str, Path] = {}
    for name, slot, mut, group in programs():
        if only and only not in name and name not in only:
            continue
        path = out_dir / (name + ".py")
        path.write_text(build_program(slot, mut, group,
                                      program_cells(slot, mut, group)).text)
        out[name] = path
    return out


# ---------------------------------------------------------------------------
# The survey.
# ---------------------------------------------------------------------------

def _tpyc():
    """Import the compiler from `REPO` on first use. Deferred so `--repo` can
    retarget the tree before anything is imported from it."""
    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    from tpyc.codegen_cpp.context import CodeGenOptions
    from tpyc.compiler import Compiler
    from tpyc.diagnostics import DiagnosticLevel, SemanticError
    from tpyc.thir.dump import _function_lines
    from tpyc.thir.lower import iter_module_callables
    return (Compiler, CodeGenOptions, DiagnosticLevel, SemanticError,
            _function_lines, iter_module_callables)


def _tag(message: str) -> str:
    """The reject tag out of a diagnostic, or a short form of the message. The
    prose moves with unrelated wording changes; the tag is the verdict."""
    if "(" in message and message.rstrip().endswith(")"):
        return message[message.rfind("(") + 1:-1]
    return message.strip().split("\n")[0][:80]


_NUMBERED = re.compile(r"__(tmp|slot|arg|temp)_\d+")
_CALLEE = re.compile(r"(?<![A-Za-z0-9_])"
                     r"(take_genrec|take_static|take_proto|take_meth"
                     r"|KNested|take|Take)(?![A-Za-z0-9_])")


def _render(fn_lines: list[str]) -> str:
    """The lowered subject statement, normalised.

    Everything from the beacon on: that is the call and whatever the argument
    forced ahead of it. The callee's own spelling differs per column by
    construction, so it is folded to one token -- what is being compared is
    the argument around it.
    """
    for i, ln in enumerate(fn_lines):
        if str(BEACON) in ln:
            body = fn_lines[i + 1:]
            break
    else:
        return "<no beacon>"
    out = " ".join(" ".join(body).split())
    out = _NUMBERED.sub(lambda m: "__" + m.group(1) + "_N", out)
    out = _CALLEE.sub("CALLEE", out)
    return out[:200]


def _owner(line: int, ranges: dict) -> object:
    for key, (lo, hi) in ranges.items():
        if lo <= line <= hi:
            return key
    return None


def survey_sema(program: str, work_dir: Path,
                cells: 'list[tuple[str, str]]') -> dict[str, list[str]]:
    """Check cells the caller expects to be SEMA errors, one MINIMAL program
    each -- the fixture, that cell's own column, that one caller function.

    Verifying a sema cell inside the full program costs a recompilation of the
    whole program, and a slot with twenty of them turned one gate item into
    forty seconds. A cell whose sema error is gone answers with whatever it
    does now (`ok` / `reject:`), so the gate still fails naming it.
    """
    _name, slot, mut, group = next(p for p in programs() if p[0] == program)
    out: dict[str, list[str]] = {}
    for cell in cells:
        got = survey(program, work_dir / "one", cells=[cell],
                     stem="%s__%s__%s" % (program, cell[0], cell[1]))
        out.update(got)
    return out


def survey(program: str, work_dir: Path,
           cells: 'list[tuple[str, str]] | None' = None,
           expect_sema: 'list[tuple[str, str]] | None' = None,
           stem: str = "") -> dict[str, list[str]]:
    """Every cell of `program`, as {cell name: [verdict, warning, render]}.

    One compilation answers every cell that lowers, because the THIR survey
    records a reject per body instead of stopping at the first. A SEMA error
    still stops the module, so the cell whose line range holds it takes the
    verdict, leaves the program, and the rest are surveyed again -- one
    recompilation per sema cell, which is what the run costs.

    `cells` narrows the matrix to a caller-supplied set. The gate passes the
    committed one, so a row the table already records as type-invalid is not
    generated at all and costs no recompilation; a full run leaves it None and
    rediscovers them.

    `expect_sema` names the cells the caller already knows refuse in sema.
    They are left OUT, so the rest are answered by a single compilation
    instead of one per sema cell; `survey_sema` checks them separately. If
    sema refuses anyway the retry loop still runs, so the failure names the
    cell that was not expected to refuse.
    """
    (Compiler, CodeGenOptions, DiagnosticLevel, SemanticError,
     _function_lines, iter_module_callables) = _tpyc()
    name, slot, mut, group = next(p for p in programs() if p[0] == program)
    work_dir.mkdir(parents=True, exist_ok=True)
    path = work_dir / ((stem or program) + ".py")
    lib = REPO / "lib" / "tpy"

    active = (list(cells) if cells is not None
              else program_cells(slot, mut, group))
    if expect_sema:
        held = set(expect_sema)
        active = [c for c in active if c not in held]
    out: dict[str, list[str]] = {}
    while True:
        built = build_program(slot, mut, group, active)
        path.write_text(built.text)
        try:
            compiler = Compiler(path, default_int="int32", lib_dirs=[lib])
            modules = compiler.compile()
            diags = list(compiler.diagnostics)
            for mod in modules:
                an = getattr(mod, "analyzer", None)
                if an is not None:
                    diags += list(getattr(an, "diagnostics", []))
            errors = [d for d in diags if d.level == DiagnosticLevel.ERROR]
            if errors:
                raise SemanticError(str(errors[0].message),
                                    getattr(errors[0], "loc", None))
        except SemanticError as exc:
            line = getattr(getattr(exc, "loc", None), "line", 0) or 0
            cell = _owner(line, built.cell_lines)
            msg = "sema:" + str(exc).strip().split("\n")[0][:80]
            if cell is None:
                # Not inside any cell -- the shared fixture or a callee
                # signature refused, so nothing in this program can be
                # surveyed. Reported rather than silently attributed.
                for c in active:
                    out[cell_name(slot, mut, *c)] = [msg, "", ""]
                return out
            out[cell_name(slot, mut, *cell)] = [msg, "", ""]
            # A row the FREE-FUNCTION column refuses is type-invalid for every
            # column, so the whole row leaves in one step instead of seven.
            drop = ({c for c in active if c[0] == cell[0]}
                    if cell[1] == "fn" else {cell})
            active = [c for c in active if c not in drop]
            if not active:
                # Nothing left to survey; compiling the bare fixture again
                # would answer nothing and doubles the cost of a one-cell
                # sema check.
                return out
            continue

        entry = next(m for m in modules if m.is_entry_point)
        ctx = compiler.collect_thir(entry, CodeGenOptions(),
                                    tolerate_reject=True)
        nodes = {f.name: f for f, _st in
                 iter_module_callables(entry.ast, entry.analyzer)
                 if f.name.startswith("cell_")}
        warnings = [d for d in diags if d.level == DiagnosticLevel.WARNING]
        for source, family in active:
            fn = nodes.get("cell_%s__%s" % (source, family))
            key = cell_name(slot, mut, source, family)
            if fn is None:
                out[key] = ["crash:no such body", "", ""]
                continue
            why = compiler.thir_reject_by_node.get(fn)
            if fn in ctx.thir_functions:
                verdict = "ok"
                render = _render(_function_lines(ctx.thir_functions[fn]))
            elif why is not None:
                verdict, render = "reject:" + str(why), ""
            else:
                verdict, render = "crash:not attempted", ""
            # The cell's OWN diagnostics: its caller function, plus the callee
            # fixture its column declares (an `Own` parameter that is never
            # consumed warns at the signature, and that warning is as much the
            # cell's answer as one inside its body).
            spans = [built.cell_lines[(source, family)]]
            if family in built.family_lines:
                spans.append(built.family_lines[family])
            mine = [w for w in warnings
                    if any(lo <= (getattr(getattr(w, "loc", None), "line", 0)
                                  or 0) <= hi for lo, hi in spans)]
            mine.sort(key=lambda w: getattr(getattr(w, "loc", None),
                                            "line", 0) or 0)
            out[key] = [verdict, _tag(mine[0].message) if mine else "", render]
        return out


def prune(table: dict[str, list[str]]) -> tuple[dict[str, list[str]], int]:
    """Drop every (slot, mutating, source) row the FREE-FUNCTION column
    answers with a sema error.

    Such a row is a type mismatch -- the source cannot produce a value of the
    slot's type at all -- so the other columns refusing it is not drift, and
    keeping the row would bury the real differences under type errors. The
    survey already drops the row when it finds it, so this only removes what a
    partial run left behind.
    """
    bad = {k.rsplit("__", 1)[0] for k, v in table.items()
           if k.endswith("__fn") and v[0].startswith("sema:")}
    kept = {k: v for k, v in table.items() if k.rsplit("__", 1)[0] not in bad}
    return kept, len(bad)


def run(work_dir: Path, names: list[str]) -> dict[str, list[str]]:
    table: dict[str, list[str]] = {}
    for i, name in enumerate(names):
        table.update(survey(name, work_dir))
        if os.environ.get("SWEEP_PROGRESS"):
            print("  %d/%d %s" % (i + 1, len(names), name), file=sys.stderr)
    return table


def routes(out_dir: Path) -> dict[str, list[str]]:
    """Which arg-table FAMILY each column actually reaches.

    A column is named after its source spelling, and two of them route
    somewhere other than the name suggests. This is measured rather than
    asserted: the reach tally on the Compiler names the deciding cell.

    Every column also reaches `plain` and `record_ctor` from the SHARED
    fixture (the fixture record's own constructor calls), which the `fn` row
    shows as the baseline -- a column's own family is what it adds to that.
    """
    Compiler, CodeGenOptions = _tpyc()[0], _tpyc()[1]
    from tpyc.thir.lower.arg_table import reached
    lib = REPO / "lib" / "tpy"
    out_dir.mkdir(parents=True, exist_ok=True)
    # A scalar slot taking a local name: a pairing every column ADMITS, so
    # each one reaches its table instead of stopping at the gate.
    slot = next(s for s in SLOTS if s.name == "i32")
    out: dict[str, list[str]] = {}
    for fam in FAMILIES:
        group = "gen" if fam.name == "gen" else "main"
        built = build_program(slot, False, group, [("local", fam.name)])
        path = out_dir / ("route_" + fam.name + ".py")
        path.write_text(built.text)
        try:
            c = Compiler(path, default_int="int32", lib_dirs=[lib])
            mods = c.compile()
            entry = next(m for m in mods if m.is_entry_point)
            c.collect_thir(entry, CodeGenOptions(), tolerate_reject=True)
            out[fam.name] = sorted({f for f, _ in reached(c)})
        except Exception as exc:  # noqa: BLE001
            out[fam.name] = ["failed: " + str(exc)[:80]]
    return out


def main() -> int:
    global REPO
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--update", action="store_true",
                    help="rewrite the committed verdict table")
    ap.add_argument("--only", default="",
                    help="only programs whose name matches this substring")
    ap.add_argument("--repo", type=Path, default=None,
                    help="the checkout to compile against")
    ap.add_argument("--out", type=Path, default=None,
                    help="where to write the generated programs")
    ap.add_argument("--routes", action="store_true",
                    help="report which arg-table family each column reaches")
    ap.add_argument("--raw", type=Path, default=None,
                    help="also dump the UNPRUNED table here, for analysis")
    args = ap.parse_args()
    if args.repo is not None:
        REPO = args.repo.resolve()

    out_dir = args.out or Path(tempfile.mkdtemp(prefix="arg_sweep_"))
    if args.routes:
        for fam, fams in routes(out_dir).items():
            print("%-8s %s" % (fam, ", ".join(fams)))
        return 0

    names = sorted(generate(out_dir, args.only))
    t0 = time.monotonic()
    raw = run(out_dir, names)
    elapsed = time.monotonic() - t0
    print("%d programs, %d cells, %.1fs" % (len(names), len(raw), elapsed),
          file=sys.stderr)
    if args.raw:
        args.raw.write_text(json.dumps(raw, indent=1, sort_keys=True) + "\n")
    table, dropped = prune(raw)
    print("%d type-invalid rows dropped" % dropped, file=sys.stderr)

    if args.update:
        if args.only:
            print("--only cannot rewrite the table: it would drop every cell "
                  "the filter excluded", file=sys.stderr)
            return 2
        EXPECTED.write_text(json.dumps(table, indent=1, sort_keys=True) + "\n")
        print("wrote %s (%d cells)" % (EXPECTED, len(table)))
        return 0

    want = json.loads(EXPECTED.read_text())
    moved = {k: (want.get(k), v) for k, v in table.items() if want.get(k) != v}
    gone = sorted(set(want) - set(table)) if not args.only else []
    if not moved and not gone:
        print("%d cells, no verdict moved" % len(table))
        return 0
    for k, (was, now) in sorted(moved.items()):
        print("MOVED %s: %s -> %s" % (k, was, now))
    for k in gone:
        print("GONE  %s: %s" % (k, want[k]))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
