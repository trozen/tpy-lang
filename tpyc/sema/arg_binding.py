"""One argument binding per call.

A call's arguments bind to the callee's parameters once, the way CPython
binds them: written positionals fill the positional parameters left to
right and overflow into `*args`, keywords match by name (the ones naming no
parameter go to a `**kwargs: Unpack[TD]` region), and every parameter left
out takes its default. `bind` is that rule for a call to its callee, and
rejects a call's argument shape there. Overload ranking places each
candidate's arguments with it too -- over the arguments' types instead of
their nodes (`overloads.bound_evidence`) -- so a candidate the call does not
bind to is not applicable, and the winner is committed at the slots it was
ranked at.

A call holds one argument list (`args` / `kwargs` / `double_star_unpack`).
Before its first commit it is the call as written. The commit
(`CallAnalyzer.commit_args`) lays it out in parameter order -- defaults
filled, `*args` packed, the `**kwargs` region last -- and stores the
binding: per parameter where its operand came from and where it sits now,
indices and names only. What the call was written with is derived from the
two (`source_view`), so a call analyzed again -- an overload winner's
argument, a lambda body tried per candidate, a pending method dispatched
again -- binds again from the source, and anything may replace an argument
node in place (a desugar, a coercion, a macro) without leaving a second
copy of the arguments behind. A layout change goes through the commit or
`relayout`.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field, replace as dc_replace
from enum import Enum
from typing import TYPE_CHECKING, Callable, Sequence

from ..parse.nodes import TpyCoerce, TpyVarargPack
from ..typesys import ParamInfo, unwrap_ref_type

if TYPE_CHECKING:
    from ..parse import TpyExpr
    from ..parse.nodes import TpyCallLike
    from ..typesys import FunctionInfo, TpyType


@dataclass(frozen=True)
class CallSource:
    """A call's arguments as written: the positionals (a `*expr` among them
    as its `TpyStarUnpack`), the keywords in written order, and a `**expr`.
    Derived from the call (`source_view`), never stored on it."""
    positional: tuple['TpyExpr', ...] = ()
    keywords: tuple[tuple[str, 'TpyExpr'], ...] = ()
    double_star: 'TpyExpr | None' = None

    def keyword(self, name: str) -> 'TpyExpr | None':
        return next((e for k, e in self.keywords if k == name), None)

    def at(self, at: 'int | str') -> 'TpyExpr | None':
        """The operand written at `at`: a position, or a keyword's name."""
        return self.positional[at] if isinstance(at, int) else self.keyword(at)

    @property
    def addresses(self) -> list['int | str']:
        """Where each operand is written, positionals first: a position,
        or a keyword's name (the `**expr` is no operand of a parameter)."""
        return [*range(len(self.positional)), *(k for k, _ in self.keywords)]

    def mapped(self, f: 'Callable[[int | str, TpyExpr], object]') -> 'CallSource':
        """This source with each operand replaced by `f(address, operand)`
        (a probe typing them: the source of the call's argument types)."""
        return CallSource(
            tuple(f(i, e) for i, e in enumerate(self.positional)),
            tuple((k, f(k, e)) for k, e in self.keywords),
            self.double_star)

    @property
    def operands(self) -> list['TpyExpr']:
        """Every argument node, positionals first."""
        out = [*self.positional, *(e for _, e in self.keywords)]
        if self.double_star is not None:
            out.append(self.double_star)
        return out


def peel_coercions(operand: 'TpyExpr') -> 'TpyExpr':
    """`operand` without the coercions the step of the node holding it
    placed (`TpyCoerce.by_step`: a call's receive, a select's arms, a
    literal's elements), which that step decides anew whenever it runs."""
    while isinstance(operand, TpyCoerce) and operand.by_step:
        operand = operand.expr
    return operand


def _same(arg: 'TpyExpr') -> 'TpyExpr':
    return arg


def committed(call) -> bool:
    """Whether `call`'s argument list is a committed layout."""
    binding = getattr(call, "binding", None)
    return isinstance(binding, ArgBinding) and binding.at is not None


def source_view(call, *, peel: bool = True) -> CallSource:
    """What `call` was written with. A committed call's is read back
    through its binding (`ArgBinding.source_view`); any other's argument
    fields are the source. `peel` takes off the coercions the receive
    placed on the operands (`peel_coercions`); without it the operands are
    the received ones."""
    if committed(call):
        return call.binding.source_view(call, peel=peel)
    p = peel_coercions if peel else _same
    return CallSource(
        tuple(p(a) for a in getattr(call, "args", ())),
        tuple((k, p(v)) for k, v in (getattr(call, "kwargs", None) or {}).items()),
        getattr(call, "double_star_unpack", None))


def relayout(call, args: 'CallSource | list[TpyExpr]',
             binding: 'ArgBinding | None') -> None:
    """The one writer of a call's argument layout besides the commit.
    With no binding, or one not committed, `args` is a `CallSource` the
    call is written with from here on (a source-indexed rewrite, a call
    taken back to its source); with a committed binding it is the
    parameter-ordered list that binding describes (a proxy's arguments
    adopted)."""
    if binding is None or binding.at is None:
        assert isinstance(args, CallSource)
        call.args = list(args.positional)
        if hasattr(call, "kwargs"):
            call.kwargs = dict(args.keywords)
        else:
            assert not args.keywords
        if hasattr(call, "double_star_unpack"):
            call.double_star_unpack = args.double_star
    else:
        call.args = list(args)
        if hasattr(call, "kwargs"):
            call.kwargs = {}
        if hasattr(call, "double_star_unpack"):
            call.double_star_unpack = None
    if hasattr(call, "binding"):
        call.binding = binding
    elif binding is not None:
        raise AssertionError(f"{type(call).__name__} holds no binding")


# Fields `relaid_copy` writes itself rather than copying.
_ARGUMENT_FIELDS = frozenset(("args", "kwargs", "double_star_unpack",
                              "binding", "explicit_receiver"))


def relaid_copy(call, copy_operand: Callable[['TpyExpr'], 'TpyExpr'],
                copy_field: Callable[[object], object] = _same,
                memo: dict | None = None, **changes):
    """A copy of `call` for a new analysis site (a deep copy, a filled
    default): its arguments are its source (`source_view`), each operand
    copied by `copy_operand`, every other field by `copy_field` unless
    `changes` gives it, and the copy is not committed."""
    src = source_view(call)
    new = copy.copy(call)
    if memo is not None:
        memo[id(call)] = new
    for name, value in vars(call).items():
        if name in changes:
            setattr(new, name, changes[name])
        elif name not in _ARGUMENT_FIELDS:
            setattr(new, name, copy_field(value))
    if hasattr(new, "explicit_receiver"):
        new.explicit_receiver = None
    relayout(new, CallSource(
        tuple(copy_operand(a) for a in src.positional),
        tuple((k, copy_operand(v)) for k, v in src.keywords),
        (copy_operand(src.double_star) if src.double_star is not None
         else None)), None)
    return new


def temp_call_args(outer, *, drop_receiver: bool = False) -> dict:
    """The argument fields of a call node sema builds to analyze `outer`
    through another route (a module function, a dotted constructor,
    `Base.m(self, ...)`, an object's `__call__`): what
    `outer` was written with (`source_view`), so analyzing `outer` again
    binds from the source. `drop_receiver`: `outer`'s first positional is
    the receiver."""
    src = source_view(outer)
    positional = src.positional[1:] if drop_receiver else src.positional
    return dict(args=list(positional), kwargs=dict(src.keywords),
                double_star_unpack=src.double_star)


def adopt_temp_call(outer, temp, *, resolution: bool = True,
                    type_args: bool = True,
                    receiver: 'TpyExpr | None' = None) -> None:
    """`outer` takes the arguments the temp call built by `temp_call_args`
    analyzed -- its argument list and binding (`relayout`) -- and, with
    `resolution`, the callee it resolved to, with `type_args` the type
    arguments that callee was instantiated at. `receiver`: the explicit receiver
    `temp_call_args` dropped, which stays `outer`'s first written
    positional."""
    binding = temp.binding
    if receiver is not None:
        assert binding is not None and binding.at is not None
        outer.explicit_receiver = receiver
        binding = dc_replace(binding, receiver=True)
    if binding is not None and binding.at is not None:
        relayout(outer, temp.args, binding)
    else:
        relayout(outer, CallSource(
            tuple(temp.args), tuple((temp.kwargs or {}).items()),
            temp.double_star_unpack), binding)
    if resolution:
        outer.resolved_function_info = temp.resolved_function_info
    if type_args:
        outer.inferred_type_args = temp.inferred_type_args
        outer.representational_subst_params = (
            temp.representational_subst_params)


class SlotKind(Enum):
    POSITIONAL = "positional"  # written positional `index`
    KEYWORD = "keyword"        # written keyword `name`
    DEFAULT = "default"        # left to the parameter's default
    VARIADIC = "variadic"      # the `*args` region: written positionals `elements`
    KWARGS = "kwargs"          # the `**kwargs` region: keywords `fields`, or the `**expr`


@dataclass(frozen=True)
class Slot:
    kind: SlotKind
    index: int = -1
    name: str = ""
    elements: tuple[int, ...] = ()
    fields: tuple[str, ...] = ()


DEFAULT_SLOT = Slot(SlotKind.DEFAULT)


@dataclass(frozen=True)
class ArgBinding:
    """Where each parameter of the callee takes its argument from: its
    origin in the call as written (`slots`, indices and names into a
    `CallSource`), and, once committed, where its operand sits in the
    call's argument list (`at`).

    `positions[i]` is the position parameter `i`'s argument takes in the
    argument list the call spells before its defaults are filled, None
    where the list leaves it out (a C++ default, or appended by the fill
    when it has none). The variadic and `**kwargs` parameters have none:
    the commit packs the one and appends the other.
    `at[i]`, set by the commit: the index of parameter `i`'s operand in
    the committed list -- the `*args` pack, the `**kwargs` region, a
    filled default -- None where the list leaves it to C++.
    `keywords`: the written keyword names in written order; `double_star`:
    the call was written with a `**expr`; `receiver`: its first written
    positional is the explicit receiver of `Base.m(self, ...)`, held on
    the call (`explicit_receiver`), not in its list.
    """
    params: Sequence['ParamInfo'] = field(compare=False, repr=False)
    slots: tuple[Slot, ...] = ()
    positions: tuple[int | None, ...] = ()
    length: int = 0
    keywords: tuple[str, ...] = ()
    double_star: bool = False
    at: tuple[int | None, ...] | None = None
    receiver: bool = False

    def __deepcopy__(self, memo) -> 'ArgBinding':
        # Indices and names over the callee's parameters, which a copy must
        # not clone.
        return self

    def expr(self, i: int, src: CallSource) -> 'TpyExpr | None':
        """The written argument parameter `i` binds, for a fixed parameter."""
        s = self.slots[i]
        if s.kind is SlotKind.POSITIONAL:
            return src.positional[s.index]
        if s.kind is SlotKind.KEYWORD:
            return src.keyword(s.name)
        return None

    def evidence(self, src: CallSource) -> list[tuple[int, 'TpyExpr']]:
        """Every written argument with the parameter it binds, in parameter
        order: a `*args` element tagged with the variadic parameter. The
        `**kwargs` region is the TypedDict construction's, not a parameter
        argument."""
        out: list[tuple[int, TpyExpr]] = []
        for i, s in enumerate(self.slots):
            if s.kind is SlotKind.VARIADIC:
                out.extend((i, src.positional[j]) for j in s.elements)
            elif s.kind in (SlotKind.POSITIONAL, SlotKind.KEYWORD):
                out.append((i, self.expr(i, src)))
        return out

    def addresses(self) -> list[tuple[int, 'int | str']]:
        """`evidence`'s entries as where each written argument is in the
        call: its parameter with its written position, or its keyword."""
        out: list[tuple[int, int | str]] = []
        for i, s in enumerate(self.slots):
            if s.kind is SlotKind.VARIADIC:
                out.extend((i, j) for j in s.elements)
            elif s.kind is SlotKind.POSITIONAL:
                out.append((i, s.index))
            elif s.kind is SlotKind.KEYWORD:
                out.append((i, s.name))
        return out

    def param_at(self, at: 'int | str') -> int | None:
        """The parameter the written argument `at` (a position, or a
        keyword's name) binds: the `*args` one for an element of it, the
        `**kwargs` one for a field of its region; None when it binds none."""
        for i, s in enumerate(self.slots):
            if isinstance(at, int):
                if ((s.kind is SlotKind.POSITIONAL and s.index == at)
                        or (s.kind is SlotKind.VARIADIC and at in s.elements)):
                    return i
            elif ((s.kind is SlotKind.KEYWORD and s.name == at)
                    or (s.kind is SlotKind.KWARGS and at in s.fields)):
                return i
        return None

    def received(self, src: CallSource,
                 operands: Sequence['TpyExpr']) -> CallSource:
        """`src` with each written argument replaced by its received
        operand: `operands` holds one per `evidence(src)` entry, in its
        order."""
        positional = list(src.positional)
        keywords = dict(src.keywords)
        n = 0
        for s in self.slots:
            if s.kind is SlotKind.VARIADIC:
                for j in s.elements:
                    positional[j] = operands[n]
                    n += 1
            elif s.kind is SlotKind.POSITIONAL:
                positional[s.index] = operands[n]
                n += 1
            elif s.kind is SlotKind.KEYWORD:
                keywords[s.name] = operands[n]
                n += 1
        assert n == len(operands)
        return CallSource(tuple(positional), tuple(keywords.items()),
                          src.double_star)

    def omitted(self) -> tuple[int, ...]:
        return tuple(i for i, s in enumerate(self.slots)
                     if s.kind is SlotKind.DEFAULT)

    @property
    def variadic(self) -> int | None:
        return next((i for i, s in enumerate(self.slots)
                     if s.kind is SlotKind.VARIADIC), None)

    def vararg_element_type(self, params: Sequence['ParamInfo']
                            ) -> 'TpyType':
        """The element type of the `*args` parameter of `params` (this
        binding's callee, as the caller spells it), which its pack holds."""
        assert self.variadic is not None
        return unwrap_ref_type(params[self.variadic].type).type_args[0]

    @property
    def kwargs(self) -> int | None:
        return next((i for i, s in enumerate(self.slots)
                     if s.kind is SlotKind.KWARGS), None)

    def kwargs_fields(self, src: CallSource) -> dict[str, 'TpyExpr']:
        """The keywords the `**kwargs` region takes, by field name."""
        i = self.kwargs
        if i is None:
            return {}
        return {k: src.keyword(k) for k in self.slots[i].fields}

    def layout(self, src: CallSource) -> list['TpyExpr | None']:
        """The argument list in parameter-slot order before the fill: each
        fixed parameter's written argument at its position (None at one
        left to its default), the `*args` elements after the fixed ones."""
        out: list[TpyExpr | None] = [None] * self.length
        for i, pos in enumerate(self.positions):
            if pos is not None:
                out[pos] = self.expr(i, src)
        va = self.variadic
        if va is not None:
            start = sum(1 for j in range(va) if self.positions[j] is not None)
            for k, j in enumerate(self.slots[va].elements):
                out[start + k] = src.positional[j]
        return out

    def layout_owners(self) -> list[int | None]:
        """The parameter each position of `layout` belongs to."""
        out: list[int | None] = [None] * self.length
        for i, pos in enumerate(self.positions):
            if pos is not None:
                out[pos] = i
        va = self.variadic
        if va is not None:
            start = sum(1 for j in range(va) if self.positions[j] is not None)
            for k in range(len(self.slots[va].elements)):
                out[start + k] = va
        return out

    def committed_slots(self) -> list[tuple[int, int]]:
        """Each fixed parameter a written argument binds, with the index
        of its operand in the committed list, in list order."""
        assert self.at is not None
        return [(i, self.at[i]) for i in self.param_order()
                if self.slots[i].kind in (SlotKind.POSITIONAL,
                                          SlotKind.KEYWORD)]

    def committed_evidence(self, call: TpyCallLike
                           ) -> list[tuple[int, TpyExpr]]:
        """`evidence` over `call`'s committed list: every operand a written
        argument became, with the parameter it binds, in list order -- a
        `*args` element read out of the pack, filled defaults and the
        `**kwargs` region left out."""
        assert self.at is not None
        out: list[tuple[int, TpyExpr]] = []
        for i in self.param_order():
            kind = self.slots[i].kind
            operand = call.args[self.at[i]]
            if kind is SlotKind.VARIADIC:
                assert isinstance(operand, TpyVarargPack)
                out.extend((i, e) for e in operand.args)
            elif kind in (SlotKind.POSITIONAL, SlotKind.KEYWORD):
                out.append((i, operand))
        return out

    def param_order(self) -> list[int]:
        """The parameters whose operand the committed list holds, in its
        order."""
        assert self.at is not None
        return sorted((i for i, a in enumerate(self.at) if a is not None),
                      key=lambda i: self.at[i])

    def source_view(self, call, *, peel: bool = True) -> CallSource:
        """The inverse of the commit's layout: what `call`, committed under
        this binding, was written with -- the operands at positional-origin
        slots by written index (a `*args` element read out of the pack),
        those at keyword-origin slots in written order, the `**kwargs`
        region's keywords read back out of its TypedDict construction by
        field (or its `**expr`), filled defaults dropped."""
        assert self.at is not None
        p = peel_coercions if peel else _same
        args = call.args
        pos: dict[int, TpyExpr] = {}
        kws: dict[str, TpyExpr] = {}
        double_star = None
        for i, s in enumerate(self.slots):
            at = self.at[i]
            if at is None:
                continue
            operand = p(args[at])
            if s.kind is SlotKind.POSITIONAL:
                pos[s.index] = operand
            elif s.kind is SlotKind.KEYWORD:
                kws[s.name] = operand
            elif s.kind is SlotKind.VARIADIC:
                assert isinstance(operand, TpyVarargPack)
                for j, e in zip(s.elements, operand.args):
                    pos[j] = p(e)
            elif s.kind is SlotKind.KWARGS:
                if self.double_star:
                    double_star = operand
                else:
                    fields = dict(source_view(operand, peel=peel).keywords)
                    for k in s.fields:
                        kws[k] = fields[k]
        assert sorted(pos) == list(range(len(pos))), "a written positional lost"
        positional = tuple(pos[j] for j in range(len(pos)))
        if self.receiver:
            positional = (call.explicit_receiver, *positional)
        return CallSource(positional,
                          tuple((k, kws[k]) for k in self.keywords
                                if k in kws),
                          double_star)

    def operands(self, *, args: 'Sequence[TpyExpr] | None' = None,
                 src: CallSource | None = None) -> 'list[TpyExpr | None]':
        """Each parameter's operand by index (None: none): the committed
        list `args` read through `at`, or the call as written (`src`), whose
        keyword-built `**kwargs` region is its own construction's operands
        (BUGS.md#kwargs-region-list-copy-unwarned), not the parameter's."""
        if args is not None:
            assert self.at is not None
            return [args[a] if a is not None else None for a in self.at]
        assert src is not None
        out: list[TpyExpr | None] = []
        for i, s in enumerate(self.slots):
            if s.kind in (SlotKind.POSITIONAL, SlotKind.KEYWORD):
                out.append(self.expr(i, src))
            elif s.kind is SlotKind.VARIADIC:
                out.append(TpyVarargPack(
                    args=[src.positional[j] for j in s.elements],
                    element_type=self.vararg_element_type(self.params)))
            elif s.kind is SlotKind.KWARGS:
                out.append(src.double_star)
            else:
                out.append(None)
        return out

    def committed(self, at: Sequence[int | None]) -> 'ArgBinding':
        """This binding, laid out with parameter `i`'s operand at `at[i]`."""
        return dc_replace(self, at=tuple(at))


@dataclass(frozen=True)
class Candidate:
    """The callee one analysis step of a call selected, bound once: the
    step's ranking, or the call's one signature, builds it, and the commit
    lays the call out, receives and stamps from it without binding or
    inferring it again.

    `matched`: the callee the call commits against -- its parameters as
    the call takes them (a generic one substituted); `declared`: how it
    takes every argument (`receive_winner_args`), None for no rule of its
    own; `binding`: the call as written bound to `matched`'s parameters;
    `evidence`: `binding.evidence` of what it was written with; `subst`:
    the type arguments the call instantiates its callee at (literal
    markers kept), at which a filled default constructs; `origin`: the
    generic declaration `matched` instantiates, None for a plain one."""
    matched: 'FunctionInfo'
    declared: 'FunctionInfo | None'
    binding: ArgBinding
    evidence: tuple[tuple[int, 'TpyExpr'], ...]
    subst: dict = field(default_factory=dict)
    origin: 'FunctionInfo | None' = None


def candidate_of(written: CallSource, binding: ArgBinding,
                 matched: 'FunctionInfo', declared: 'FunctionInfo | None',
                 subst: dict | None = None,
                 origin: 'FunctionInfo | None' = None) -> Candidate:
    """The `Candidate` of `matched`, whose parameters the call written as
    `written` binds by `binding`."""
    return Candidate(matched, declared, binding,
                     tuple(binding.evidence(written)), dict(subst or {}),
                     origin)


@dataclass(frozen=True)
class BindReject:
    """Why a call's arguments do not bind to a callee, as the diagnostic
    says it."""
    message: str


def arity_error_msg(name: str, min_args: int, max_args: int, got: int) -> str:
    """Format an arity mismatch error message."""
    if min_args == max_args:
        return f"'{name}' expects {max_args} argument(s), got {got}"
    return f"'{name}' expects {min_args} to {max_args} arguments, got {got}"


def positional_param_index(params: Sequence['ParamInfo'], i: int) -> int | None:
    """The parameter written positional argument `i` binds: the `i`-th
    positional parameter, past them the `*args` parameter, else none."""
    fixed = [j for j, p in enumerate(params)
             if not p.keyword_only and not p.is_variadic and not p.is_kwargs]
    if i < len(fixed):
        return fixed[i]
    return next((j for j, p in enumerate(params) if p.is_variadic), None)


def bind(written: CallSource, params: Sequence['ParamInfo'],
         name: str) -> ArgBinding | BindReject:
    """Bind `written` (`source_view`) to `params` of the callee a
    diagnostic calls `name`.

    The `**expr` of a call is the `**kwargs` parameter's whole region, alone:
    a callee without one rejects it (a `**td` into named parameters is not
    unpacked), as it rejects keywords beside it. The caller checks it
    against the TypedDict."""
    pos = written.positional
    n = len(pos)
    va = next((i for i, p in enumerate(params) if p.is_variadic), None)
    kw_param = next((i for i, p in enumerate(params) if p.is_kwargs), None)
    if written.double_star is not None:
        if kw_param is None:
            return BindReject(f"'{name}' does not accept **kwargs")
        if written.keywords:
            return BindReject("Cannot mix keyword arguments with **unpacking")
    fixed_pos = [i for i, p in enumerate(params)
                 if not p.keyword_only and not p.is_variadic and not p.is_kwargs]
    kwonly = [i for i, p in enumerate(params) if p.keyword_only]
    name_to_index = {p.name: i for i, p in enumerate(params)
                     if not p.is_variadic}

    # A keyword naming no parameter a keyword can bind is a `**kwargs`
    # field: a positional-only or the `*args` name included, as in CPython
    # (`def f(x, /, **kw)`: `f(1, x=2)` gives `kw == {'x': 2}`).
    regular: dict[str, TpyExpr] = {}
    fields: list[str] = []
    named = {p.name for p in params
             if not p.is_kwargs and not p.is_variadic and not p.positional_only}
    for k, e in written.keywords:
        if kw_param is not None and k not in named:
            fields.append(k)
        else:
            regular[k] = e

    def reject(msg: str) -> BindReject:
        return BindReject(f"'{name}' {msg}")

    # CPython's order: the keywords first, then the positional count, then
    # what is missing.
    for k in regular:
        if k not in name_to_index:
            return reject(f"got unexpected keyword argument '{k}'")
        if params[name_to_index[k]].positional_only:
            return reject(f"parameter '{k}' is positional-only and "
                          f"cannot be passed by keyword")
    for pname, idx in name_to_index.items():
        if (pname in regular and idx < n
                and not params[idx].keyword_only):
            return reject(f"got multiple values for argument '{pname}'")

    min_args = sum(1 for p in params
                   if not p.has_default and not p.keyword_only
                   and not p.is_variadic and not p.is_kwargs)
    if va is None and n > len(fixed_pos):
        takes = (f"{len(fixed_pos)} positional argument(s)"
                 if min_args == len(fixed_pos)
                 else f"{min_args} to {len(fixed_pos)} positional arguments")
        return BindReject(f"'{name}' takes {takes}, got {n}")

    if not regular:
        for i in kwonly:
            if not params[i].has_default:
                return reject(
                    f"missing required keyword argument: '{params[i].name}'")
        if kwonly:
            for i in fixed_pos[n:]:
                if not params[i].has_default:
                    return reject(
                        f"missing required argument: '{params[i].name}'")
    else:
        if va is not None:
            for i in fixed_pos[n:]:
                if params[i].name not in regular and not params[i].has_default:
                    return reject(
                        f"missing required argument: '{params[i].name}'")
            for i in kwonly:
                if params[i].name not in regular and not params[i].has_default:
                    return reject(f"missing required keyword argument: "
                                  f"'{params[i].name}'")
        else:
            rightmost = max([n - 1] + [name_to_index[k] for k in regular])
            for i in range(min(rightmost + 1, len(params))):
                p = params[i]
                if (i >= n and p.name not in regular and not p.has_default
                        and not p.is_kwargs):
                    return reject(f"missing required argument: '{p.name}'")
            for i in kwonly:
                if (i > rightmost and params[i].name not in regular
                        and not params[i].has_default):
                    return reject(f"missing required keyword argument: "
                                  f"'{params[i].name}'")

    slots: list[Slot] = []
    for i, p in enumerate(params):
        if p.is_variadic:
            slots.append(Slot(SlotKind.VARIADIC,
                              elements=tuple(range(len(fixed_pos), n))))
        elif p.is_kwargs:
            slots.append(Slot(SlotKind.KWARGS, fields=tuple(fields)))
        elif not p.keyword_only and fixed_pos.index(i) < n:
            slots.append(Slot(SlotKind.POSITIONAL, index=fixed_pos.index(i)))
        elif p.name in regular:
            slots.append(Slot(SlotKind.KEYWORD, name=p.name))
        else:
            slots.append(DEFAULT_SLOT)

    # Which parameters the argument list spells before the fill: a
    # keyword call or a keyword-only parameter spells every fixed one up to
    # the last it binds (all of them past a `*args`), a positional call
    # only what it writes.
    positions: list[int | None] = [None] * len(params)
    if va is not None:
        n_elems = max(0, n - len(fixed_pos))
        full = bool(regular) or bool(kwonly)
        spelled = fixed_pos if full else fixed_pos[:n]
        at = 0
        for i in spelled:
            positions[i] = at
            at += 1
        at += n_elems
        if full:
            for i in kwonly:
                positions[i] = at
                at += 1
        length = at
    else:
        if regular:
            length = max([n] + [name_to_index[k] + 1 for k in regular])
        elif kwonly:
            length = len(fixed_pos) + len(kwonly)
        else:
            length = n
        order = fixed_pos + kwonly
        for at, i in enumerate(order[:length]):
            positions[i] = at

    # Excess positionals were refused above, so only a missing required
    # positional is left for the count; the `**kwargs` region is no argument
    # of it.
    count = length
    max_args = (2**31 if va is not None
                else len(params) - (1 if kw_param is not None else 0))
    if count < min_args or count > max_args:
        return BindReject(arity_error_msg(name, min_args, max_args, count))

    return ArgBinding(params, tuple(slots), tuple(positions), length,
                      keywords=tuple(k for k, _ in written.keywords),
                      double_star=written.double_star is not None)
