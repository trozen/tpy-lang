"""
TurboPython Operator Resolution

Binary and unary operator resolution using the type registry.
"""

from __future__ import annotations
from dataclasses import replace as dc_replace
from typing import TYPE_CHECKING, Callable, NamedTuple

from ..typesys import (
    TpyType, IntLiteralType, FloatLiteralType, LiteralType,
    ResolvedBinop, ResolvedUnaryop, FunctionInfo, ParamInfo, RecordInfo,
    FLOAT, PendingContainerType, OwnType, unwrap_ref_type,
    unwrap_readonly, resolve_int_literals,
    substitute_type_params_simple, is_any_float_type, answers_as_call,
)
from ..parse import TpyAugAssign, TpyBinOp, TpyExpr
from .arg_binding import BindReject, CallSource
from .overloads import (CallShape, bound_evidence, call_ranking,
                        filter_receiver_overloads,
                        OverloadAmbiguityError, converts_literal_to_base,
                        pins_const_receiver)
from .literal_utils import numeric_literal_type
from .receiver_calls import receiver_is_readonly
from ..coercions import (resolve_coercion, context_free_wrap_template,
                         CoercionContext, coercion_refuses_value)
from ..type_def_registry import is_fixed_int_type, is_big_int_type
from tpyc import modules as builtin_modules

if TYPE_CHECKING:
    from .context import SemanticContext
    from .protocols import ProtocolChecker as ProtocolCheckerObj
    from .type_ops import TypeOperations
    from .compatibility import TypeCompatibility
    ProtocolChecker = Callable[[TpyType, TpyType], bool]

from .bound_check import raise_if_class_param_bound_violated


# C++ templates for user-defined dunder methods (used for unified operator resolution)
# These use C++ operator syntax which works for user types with operator overloads
DUNDER_CPP_TEMPLATES: dict[str, str] = {
    # Binary operators
    "__add__": "({self}) + ({0})",
    "__sub__": "({self}) - ({0})",
    "__mul__": "({self}) * ({0})",
    "__truediv__": "({self}) / ({0})",
    # C++ has no `//`; lower to a direct method call so `//` stays distinct
    # from `/` (a type may define both __truediv__ and __floordiv__ with the
    # same operand type -- e.g. timedelta -- which cannot share operator/).
    "__floordiv__": "({self}).__floordiv__({0})",
    "__mod__": "({self}) % ({0})",
    "__pow__": "std::pow({self}, {0})",
    "__lshift__": "({self}) << ({0})",
    "__rshift__": "({self}) >> ({0})",
    "__and__": "({self}) & ({0})",
    "__or__": "({self}) | ({0})",
    "__xor__": "({self}) ^ ({0})",
    # Comparison operators
    "__eq__": "({self}) == ({0})",
    "__ne__": "({self}) != ({0})",
    "__lt__": "({self}) < ({0})",
    "__le__": "({self}) <= ({0})",
    "__gt__": "({self}) > ({0})",
    "__ge__": "({self}) >= ({0})",
    # Reverse operators
    "__radd__": "({0}) + ({self})",
    "__rsub__": "({0}) - ({self})",
    "__rmul__": "({0}) * ({self})",
    "__rtruediv__": "({0}) / ({self})",
    "__rfloordiv__": "({self}).__rfloordiv__({0})",
    "__rmod__": "({0}) % ({self})",
    "__rpow__": "std::pow({0}, {self})",
    "__rlshift__": "({0}) << ({self})",
    "__rrshift__": "({0}) >> ({self})",
    "__rand__": "({0}) & ({self})",
    "__ror__": "({0}) | ({self})",
    "__rxor__": "({0}) ^ ({self})",
    # Unary operators
    "__pos__": "+({self})",
    "__neg__": "-({self})",
    "__invert__": "~({self})",
    # Conversion methods (used for operator promotion)
    "__int__": "({self}).__int__()",
}


# Every dunder the resolver below dispatches an operator to. Not the
# codegen friend-shim tables: a use renders the dunder without its template
# argument whether it goes through a friend operator (`+`, `==`, `+` with
# the record on the right) or a direct call (`+=`, `//`, unary `-`), and
# `readonly_form_t<C>` cannot be deduced at either, so a user method among
# all of these with a type parameter of its own fails the C++ build.
OPERATOR_DUNDERS: frozenset[str] = frozenset(
    list(builtin_modules.BINOP_TO_METHOD.values())
    + list(builtin_modules.BINOP_TO_RMETHOD.values())
    + list(builtin_modules.AUGOP_TO_IMETHOD.values())
    + list(builtin_modules.UNARYOP_TO_METHOD.values()))


def literal_home(t: TpyType, other: TpyType,
                 other_effective: TpyType) -> TpyType | None:
    """The type numeric literal operand `t` takes beside the operator's
    other operand `other` (of effective type `other_effective`), as NumPy's
    weak scalars do: beside a `float` / `float32` that type, always; beside
    a fixed int an int literal the fixed type when the coercion row admits
    its value, beside an `int` an `int`; an int literal beside a float
    literal is a `float`. None when it has no home there and is priced at
    its own (the default int width, `float`), as a call argument is."""
    lit = numeric_literal_type(t)
    if lit is None:
        return None
    beside = numeric_literal_type(other)
    if isinstance(beside, FloatLiteralType):
        return FLOAT if isinstance(lit, IntLiteralType) else None
    if isinstance(beside, IntLiteralType):
        return None
    if is_any_float_type(other_effective):
        return other_effective
    if not isinstance(lit, IntLiteralType) or not (
            is_fixed_int_type(other_effective) or is_big_int_type(other_effective)):
        return None
    if coercion_refuses_value(lit, other_effective, CoercionContext.ARG):
        return None
    return other_effective


def _literal_beside(t: TpyType, effective: TpyType, other: TpyType,
                    other_effective: TpyType) -> TpyType:
    """The effective type of operand `t` next to `other` for the record
    lookup: an int literal whose home there is a fixed int is of that type
    (`literal_home`); any other operand keeps its own."""
    home = literal_home(t, other, other_effective)
    return home if home is not None and is_fixed_int_type(home) else effective


def _answers_converted(operand: TpyType, param: TpyType) -> bool:
    """Whether a builtin type's dunder set answers with a winner that
    converts `operand` to its parameter `param`: a numeric literal
    (`numeric_literal_type`, the operand the literal pin renders at the
    parameter's own type), or a `Literal[...]` value converted to its own
    base type. Converting any other value is left to the resolver's later
    steps, so `n32 + big` is an `int` there, not a narrowed `int32`."""
    return (numeric_literal_type(operand) is not None
            or converts_literal_to_base(operand, param))


def _operand_exprs(loc_node: object) -> tuple[TpyExpr | None, TpyExpr | None]:
    """The (left, right) operand expressions of the operator `loc_node`
    spells: a binary operator's, an augmented assignment's target and
    value; None for an operand it does not spell."""
    if isinstance(loc_node, TpyBinOp):
        return loc_node.left, loc_node.right
    if isinstance(loc_node, TpyAugAssign):
        return loc_node.target, loc_node.value
    return None, None


class OperatorAmbiguity(NamedTuple):
    """A dunder set whose ranking ties at the top for the operand -- a
    call's "Ambiguous overload" answer, returned as a value: a caller that
    probes the resolver speculatively goes on, the one that decides the
    operator raises it (`ExpressionAnalyzer.decide_operator`)."""
    method_name: str
    candidates: tuple[FunctionInfo, ...]


class RankedDunder(NamedTuple):
    """The variant a dunder set answers with: the method, the substitution
    to apply to it, and whether its call passes the receiver const so C++
    runs it (`pins_const_receiver`)."""
    method: FunctionInfo
    subst: dict[str, TpyType]
    const_receiver: bool = False


def _type_entries(subst: dict[str, TpyType | int]) -> dict[str, TpyType]:
    """The entries of a receiver substitution a type substitution applies
    (an int-kind arg such as `N` has no type to put in its place)."""
    return {k: v for k, v in subst.items() if isinstance(v, TpyType)}


class OperatorResolver:
    """Resolves binary and unary operators using the type registry."""

    def __init__(
        self, ctx: SemanticContext,
        type_ops: 'TypeOperations', protocols: 'ProtocolCheckerObj',
        compat: 'TypeCompatibility',
    ):
        self.ctx = ctx
        self.type_ops = type_ops
        # Used to enforce class-shadowed method type-param bounds at dispatch.
        self.protocols = protocols
        self.compat = compat

    def _check_class_bounds(
        self, method: FunctionInfo, receiver_type: TpyType,
        class_subst: dict[str, 'TpyType | int'], loc_node,
    ) -> None:
        """Raise if the receiver doesn't satisfy a class-shadowed method bound.

        Operator dispatch bypasses `_analyze_generic_method_call` (which runs
        the same check), so every resolve_* path must invoke this so the
        sema-level error matches what codegen's `requires` clause catches at
        C++ instantiation time.
        """
        # Cheap method-level guard first: short-circuits the common case
        # (every operator dispatch where the matched method has no bounds).
        if not method.type_param_bounds:
            return
        # readonly[Box[T]] conforms to any protocol Box[T] does -- mirror that
        # unwrap here so the bound check operates on the inner record.
        record = self.ctx.registry.get_record_for_type(unwrap_readonly(receiver_type))
        if record is None or not record.type_params:
            return
        raise_if_class_param_bound_violated(
            method, record.type_params, class_subst,
            self.protocols.type_conforms_to_protocol,
            self.ctx.error, loc_node,
        )

    def _resolve_pending_types(self, tpy_type: TpyType) -> TpyType:
        """Resolve pending/structural types (PendingListType, LiteralType).

        Preserves IntLiteralType for flexible overload matching.
        """
        if isinstance(tpy_type, PendingContainerType):
            return tpy_type.spelled(tpy_type.parts())
        if isinstance(tpy_type, LiteralType):
            return tpy_type.base_type
        return tpy_type

    def get_effective_type_for_binop(self, tpy_type: TpyType) -> TpyType:
        """Fully resolve type for registry lookup (also resolves IntLiteralType/FloatLiteralType)."""
        tpy_type = unwrap_ref_type(tpy_type)
        if isinstance(tpy_type, OwnType):
            tpy_type = tpy_type.wrapped
        if isinstance(tpy_type, IntLiteralType):
            return self.ctx.default_int_for_literal(tpy_type)
        if isinstance(tpy_type, FloatLiteralType):
            return FLOAT
        if isinstance(tpy_type, LiteralType):
            return tpy_type.base_type
        return self._resolve_pending_types(tpy_type)

    def _build_type_subst(
        self, receiver_type: TpyType, arg_effective: TpyType,
    ) -> dict[str, TpyType | int]:
        """Build type param substitution from a concrete receiver type,
        int-kind args included (`N` of an `ArrayList[T, N]` receiver);
        `_type_entries` is the part a type substitution applies.

        When the receiver has IntLiteralType element(s), adapts to the arg's
        concrete element type if available, otherwise resolves to default int.
        """
        full = self.type_ops.build_type_substitution(receiver_type)
        subst: dict[str, TpyType | int] = {
            k: v for k, v in full.items() if isinstance(v, TpyType)}
        if not subst:
            return dict(full)
        arg_params = builtin_modules.extract_type_params(arg_effective)
        for name, typ in list(subst.items()):
            if isinstance(typ, (IntLiteralType, FloatLiteralType)):
                arg_val = arg_params.get(name)
                if arg_val is not None and not isinstance(arg_val, (IntLiteralType, FloatLiteralType)):
                    subst[name] = arg_val
                else:
                    subst[name] = resolve_int_literals(typ, self.ctx.default_int_for_literal)
            else:
                subst[name] = resolve_int_literals(typ, self.ctx.default_int_for_literal)
        return {**full, **subst}

    def _rank_set(
        self, overloads: list[FunctionInfo], operand: TpyType,
        type_subst: dict[str, TpyType | int],
        receiver: TpyType, receiver_expr: TpyExpr | None,
        home: TpyType | None = None,
    ) -> RankedDunder | OperatorAmbiguity | None:
        """`rank_dunder_set` for an operator's one operand. A numeric
        literal operand ranks at its `home` beside the other operand when
        it has one. A record's set answers as the call does
        (`answers_as_call`); any other answers when the winner takes the
        operand as it is, or converts it as `_answers_converted` admits,
        and leaves any other conversion to the later steps, as it does a
        tie fewer than two of whose candidates would answer."""
        arg = home if home is not None else operand
        as_call = answers_as_call(receiver)
        return self.rank_dunder_set(
            overloads, [arg], type_subst, receiver, receiver_expr,
            admits=None if as_call else (
                lambda m, strict: strict or _answers_converted(
                    operand, m.params[0].type)))

    def rank_dunder_set(
        self, overloads: list[FunctionInfo], args: list[TpyType],
        type_subst: dict[str, TpyType | int],
        receiver: TpyType, receiver_expr: TpyExpr | None,
        admits: Callable[[FunctionInfo, bool], bool] | None = None,
    ) -> RankedDunder | OperatorAmbiguity | None:
        """Rank one dunder set for the arguments `args` as the named call
        to it ranks the set (`call_ranking`): the receiver's type
        params substituted (int-kind ones included), its kind the ranking's
        first component (`rank_overload_candidates`), the method's own type
        params inferred from the arguments. `admits(variant, strict)` says
        whether a set that does not answer as the call (a builtin type's)
        answers with that winner; a tie answers when at least two tied
        candidates would. Returns the winner, with whether its call pins
        the receiver const (`pins_const_receiver`), or the tie as a
        value."""
        shape = CallShape(len(args))
        cands = filter_receiver_overloads(
            [m for m in overloads if shape.binding(m) is not None],
            is_consuming_receiver=False)
        if not cands:
            return None
        ranking = call_ranking(self.ctx, self.protocols, self.type_ops,
                               self.compat)
        views = [self.type_ops.substitute_method_type_params(m, type_subst)
                 if type_subst else m for m in cands]
        readonly = receiver_is_readonly(self.ctx, receiver_expr, receiver)
        try:
            pick = ranking.rank(views, args, is_readonly_receiver=readonly)
        except OverloadAmbiguityError as e:
            tied = tuple(c for c in e.candidates
                         if admits is None or admits(c, not e.converted))
            return (OperatorAmbiguity(cands[0].name, tied)
                    if len(tied) > 1 else None)
        if pick.winner is None:
            return None
        if admits is not None and not admits(views[pick.winner],
                                             pick.winner in pick.strict):
            return None
        found = self.bind_winner(cands[pick.winner], args, type_subst)
        if found is None:
            return None
        return found._replace(const_receiver=pins_const_receiver(
            cands[pick.winner], cands, readonly))

    def bind_winner(
        self, method: FunctionInfo, args: list[TpyType],
        type_subst: dict[str, TpyType | int],
    ) -> RankedDunder | None:
        """The ranked winner with its substitution: the receiver's type
        params, and a type param of the method's own bound from the
        arguments as a call binds it (a class-shadowed name stays the
        class's)."""
        bind = _type_entries(type_subst)
        own_params = [tp for tp in method.type_params if tp not in type_subst]
        if not own_params:
            return RankedDunder(method, bind)
        params = []
        for param in method.params:
            param_type = unwrap_ref_type(param.type)
            if bind:
                param_type = substitute_type_params_simple(param_type, bind)
            params.append(dc_replace(param, type=param_type))
        view = dc_replace(method, params=params, type_params=own_params)
        evidence = bound_evidence(view, CallSource(tuple(args)))
        if isinstance(evidence, BindReject):
            return None
        bound = self.type_ops.infer_type_params_for_function(
            view, [t for _, t in evidence],
            self.protocols.type_conforms_to_protocol,
            slots=[i for i, _ in evidence])
        if bound is None:
            return None
        return RankedDunder(method, {**bind, **bound})

    def _make_resolved(
        self, found: RankedDunder,
        receiver_type: TpyType, loc_node, left_wrapper: str = "{expr}",
        right_wrapper: str = "{expr}", is_reverse: bool = False,
        promotion: FunctionInfo | None = None, widens_operand: bool = False,
    ) -> ResolvedBinop:
        """Create ResolvedBinop with type params substituted in method signature."""
        method, type_subst = found.method, found.subst
        # Bound check before substitution rewrites `method` (bounds are on the
        # original FunctionInfo and indexed by class-shadow names). Raises on
        # violation -- the resolver never returns a result carrying one.
        self._check_class_bounds(method, receiver_type, type_subst, loc_node)
        if type_subst:
            new_params = [
                dc_replace(p, type=substitute_type_params_simple(p.type, type_subst))
                for p in method.params
            ]
            new_return = substitute_type_params_simple(method.return_type, type_subst)
            method = dc_replace(method, params=new_params, return_type=new_return,
                                canonical_fi=method.root)
        # Unwrap Ref from return type -- Ref is a codegen-level concern,
        # sema expression types should not carry it.
        ret = unwrap_ref_type(method.return_type)
        if ret is not method.return_type:
            method = dc_replace(method, return_type=ret, canonical_fi=method.root)
        if type_subst:
            # Rebuild receiver_type from subst to resolve IntLiteralType elements
            # (a literal receiver like [10, 20] carries IntLiteralType inner types
            # that must not leak into codegen's receiver render).
            # Use inner_types() as the source of truth for the reconstruction -- it
            # defines exactly how many (and which) inner types the type has. params_map
            # may have more entries (e.g. Tspan for PtrType), but with_inner_types only
            # accepts len(inner_types()) values.
            inner = receiver_type.inner_types()
            if inner:
                params_map = builtin_modules.extract_type_params(receiver_type)
                param_names = list(params_map.keys())
                new_inner = tuple(
                    type_subst.get(param_names[i], inner[i]) if i < len(param_names) else inner[i]
                    for i in range(len(inner))
                )
                receiver_type = receiver_type.with_inner_types(new_inner)
        return ResolvedBinop(
            method=method,
            left_wrapper=left_wrapper,
            right_wrapper=right_wrapper,
            is_reverse=is_reverse,
            receiver_type=receiver_type,
            promotion=promotion,
            widens_operand=widens_operand,
            const_receiver=found.const_receiver,
        )

    def received_params(self, result: ResolvedBinop) -> list[ParamInfo]:
        """The resolved dunder's operand parameter as its explicit call
        receives the operand: substituted at the receiver's type args,
        int-kind ones included (`N` of an `ArrayList[T, N]` receiver)."""
        method = result.method
        full = (self.type_ops.build_type_substitution(result.receiver_type)
                if result.receiver_type is not None else {})
        if any(not isinstance(v, TpyType) for v in full.values()):
            method = self.type_ops.substitute_method_type_params(method, full)
        return method.params[:1]

    def dunder_overloads(self, record: RecordInfo | None,
                         name: str) -> list[FunctionInfo]:
        """The overloads of dunder `name` on `record` as its explicit call
        finds them: the record's own, else the nearest parent's
        (`ProtocolChecker.lookup_record_method_overloads`), with the
        parent's type params substituted as the child spells them."""
        if record is None:
            return []
        overloads, parent_subst = self.protocols.lookup_record_method_overloads(
            record, name)
        if parent_subst:
            overloads = [self.type_ops.substitute_method_type_params(m, parent_subst)
                         for m in overloads]
        return overloads

    def receiver_dunders(self, record: RecordInfo | None,
                         name: str) -> list[FunctionInfo]:
        """The overloads answering dunder `name` at one receiver: its own
        (`dunder_overloads`), and for a `__ne__` it does not define, its
        `__eq__`, which `object.__ne__` negates -- so `l != r` asks `l`
        before the reflected `r.__ne__`, as Python does."""
        overloads = self.dunder_overloads(record, name)
        if not overloads and name == "__ne__":
            overloads = self.dunder_overloads(record, "__eq__")
        return overloads

    def forward_dunders(self, left_type: TpyType, op: str) -> list[FunctionInfo]:
        """The overloads `resolve_binop` tries first for `left op right`:
        the left operand's own dunder (`left.__add__`)."""
        method_name = builtin_modules.BINOP_TO_METHOD.get(op)
        if not method_name:
            return []
        record = self.ctx.registry.get_record_for_type(
            self.get_effective_type_for_binop(left_type))
        return self.receiver_dunders(record, method_name)

    def resolve_binop(
        self, left_type: TpyType, op: str, right_type: TpyType,
        loc_node=None,
    ) -> ResolvedBinop | OperatorAmbiguity | None:
        """`_resolve_binop`, with the fact that an int literal operand made
        the operation compute in `int` (`ResolvedBinop.wide_literal`)."""
        result = self._resolve_binop(left_type, op, right_type, loc_node)
        # A comparison computes no value: a literal compares by value.
        if (isinstance(result, ResolvedBinop)
                and op in builtin_modules.BINOP_TO_RMETHOD):
            wide = (self._wide_literal(left_type, right_type)
                    or self._wide_literal(right_type, left_type))
            if wide is not None:
                result = dc_replace(result, wide_literal=wide)
        return result

    def _wide_literal(self, t: TpyType, other: TpyType) -> IntLiteralType | None:
        """`t` when it is an int literal outside the default int that has no
        home beside fixed-width `other` (`literal_home`), else None."""
        if not isinstance(t, IntLiteralType) or t.value is None:
            return None
        other_effective = self.get_effective_type_for_binop(other)
        if not is_fixed_int_type(other_effective):
            return None
        if literal_home(t, other, other_effective) is not None:
            return None
        return t if is_big_int_type(self.ctx.default_int_for_literal(t)) else None

    def _resolve_binop(
        self, left_type: TpyType, op: str, right_type: TpyType,
        loc_node=None,
    ) -> ResolvedBinop | OperatorAmbiguity | None:
        """Resolve binary operator using registry.

        Handles both builtin types and user-defined types with dunder methods.
        User-defined methods have cpp_template set to C++ operator syntax.

        Generic methods (e.g. list[T].__add__) have their type params substituted
        based on the receiver's concrete element type.

        Tries in order:
        1. left.__add__(right) - direct match
        2. If left has __int__ returning right's type, promote left and use right's __add__
        3. right.__radd__(left) - reverse operator; for a comparison, a
           record right operand's reflected comparison (`right.__gt__(left)`)
        4. If right has __int__ returning left's type, promote right and use left's __add__
        5. Two fixed ints of different types: the operand a slot of the other's
           type would accept converts, and the wider type's operator applies
           (arithmetic and bitwise only: shifts' counts do not widen into the
           left type, and comparisons compare mixed widths already)
        """
        method_name = builtin_modules.BINOP_TO_METHOD.get(op)
        rmethod_name = builtin_modules.BINOP_TO_RMETHOD.get(op)
        if not method_name:
            return None

        # Get effective types (IntLiteralType -> configured default int type,
        # PendingListType -> ListType)
        left_effective = self.get_effective_type_for_binop(left_type)
        right_effective = self.get_effective_type_for_binop(right_type)
        # A numeric literal ranks at its home beside the other operand.
        left_home = literal_home(left_type, right_type, right_effective)
        right_home = literal_home(right_type, left_type, left_effective)
        # A literal has no width of its own: beside a fixed int it is that
        # type, so the fixed side's operator applies whichever side the
        # literal is on (`1 << k` with `k: int64` shifts an int64).
        left_effective = _literal_beside(left_type, left_effective, right_type, right_effective)
        right_effective = _literal_beside(right_type, right_effective, left_type, left_effective)

        left_record = self.ctx.registry.get_record_for_type(left_effective)
        right_record = self.ctx.registry.get_record_for_type(right_effective)

        # Build type substitution maps for generic types (e.g. list[T] -> list[int32])
        left_subst = self._build_type_subst(left_effective, right_effective)
        right_subst = self._build_type_subst(right_effective, left_effective)

        # Each operand is ranked as analyzed: an undecided container as it
        # is, a `Literal[...]` as a literal.
        left_arg = unwrap_ref_type(left_type)
        right_arg = unwrap_ref_type(right_type)
        left_expr, right_expr = _operand_exprs(loc_node)

        # 1. Try direct: left.__add__(right)
        if left_record:
            overloads = self.forward_dunders(left_effective, op)
            found = self._rank_set(overloads, right_arg, left_subst,
                                   left_type, left_expr, right_home)
            if isinstance(found, OperatorAmbiguity):
                return found
            if found:
                return self._make_resolved(found, left_effective, loc_node)

        # 2. Try promoting left to right's type via __int__
        if left_record and right_record:
            int_overloads = self.dunder_overloads(left_record, "__int__")
            if int_overloads:
                int_method = int_overloads[0]
                promoted_type = int_method.return_type
                # Check if promoted type matches right's type
                if self.ctx.registry.get_record_for_type(promoted_type) == right_record:
                    right_overloads = self.dunder_overloads(right_record, method_name)
                    found = self._rank_set(right_overloads, right_arg, right_subst,
                                           promoted_type, None, right_home)
                    if isinstance(found, OperatorAmbiguity):
                        return found
                    if found:
                        return self._make_resolved(
                            found, right_effective, loc_node,
                            left_wrapper=int_method.cpp_template or "{expr}",
                            promotion=int_method,
                        )

        # 3. Try reverse: right.__radd__(left). A comparison asks a record
        # on the right for its reflected comparison (`3 < c` is
        # `c.__gt__(3)`); a builtin type's comparison has no reflected step.
        # A record on the left that defines the comparison would be asked
        # first; one whose dunder refuses the operand cannot be (Python
        # would still call it), so the reflected one does not answer.
        reflected = rmethod_name or (
            builtin_modules.COMPARE_TO_REFLECTED_METHOD.get(op)
            if answers_as_call(right_effective) and not (
                answers_as_call(left_effective)
                and self.receiver_dunders(left_record, method_name))
            else None)
        if right_record and reflected:
            overloads = self.receiver_dunders(right_record, reflected)
            found = self._rank_set(overloads, left_arg, right_subst,
                                   right_type, right_expr, left_home)
            if isinstance(found, OperatorAmbiguity):
                return found
            if found:
                return self._make_resolved(
                    found, right_effective, loc_node, is_reverse=True,
                )

        # 4. Try promoting right to left's type via __int__, then use left's operator
        if right_record and left_record:
            int_overloads = self.dunder_overloads(right_record, "__int__")
            if int_overloads:
                int_method = int_overloads[0]
                promoted_type = int_method.return_type
                # Check if promoted type matches left's type
                if self.ctx.registry.get_record_for_type(promoted_type) == left_record:
                    left_overloads = self.dunder_overloads(left_record, method_name)
                    found = self._rank_set(left_overloads, promoted_type, left_subst,
                                           left_type, left_expr)
                    if isinstance(found, OperatorAmbiguity):
                        return found
                    if found:
                        return self._make_resolved(
                            found, left_effective, loc_node,
                            right_wrapper=int_method.cpp_template or "{expr}",
                            promotion=int_method,
                        )

        # 5. Mixed fixed-width ints: operators use the slot relation, so the
        # conversion is the coercion row's own render. Comparisons (no
        # reflected dunder) already compare mixed widths natively.
        if rmethod_name and op not in ("<<", ">>"):
            if wrap := self._fixed_int_widening_wrap(right_effective, left_effective):
                overloads = self.dunder_overloads(left_record, method_name)
                found = self._rank_set(overloads, left_effective, left_subst,
                                       left_type, left_expr)
                if isinstance(found, OperatorAmbiguity):
                    return found
                if found:
                    return self._make_resolved(
                        found, left_effective, loc_node,
                        right_wrapper=wrap, widens_operand=True,
                    )
            elif wrap := self._fixed_int_widening_wrap(left_effective, right_effective):
                overloads = self.dunder_overloads(right_record, method_name)
                found = self._rank_set(overloads, right_effective, right_subst,
                                       right_type, right_expr)
                if isinstance(found, OperatorAmbiguity):
                    return found
                if found:
                    return self._make_resolved(
                        found, right_effective, loc_node,
                        left_wrapper=wrap, widens_operand=True,
                    )

        return None

    @staticmethod
    def _fixed_int_widening_wrap(narrow: TpyType, wide: TpyType) -> str | None:
        """The `{expr}` template converting fixed int `narrow` to fixed int
        `wide` when a `wide` slot would accept it, else None."""
        if not (is_fixed_int_type(narrow) and is_fixed_int_type(wide)):
            return None
        coercion = resolve_coercion(narrow, wide, None)
        if coercion is None or not coercion.widening_safe:
            return None
        template = context_free_wrap_template(coercion, narrow, wide)
        return template.replace("{0}", "{expr}") if template else None

    def resolve_aug_inplace(
        self, target_type: TpyType, op: str, value_type: TpyType,
        loc_node=None,
    ) -> ResolvedBinop | OperatorAmbiguity | None:
        """Resolve in-place augmented assignment operator (e.g. __iadd__, __ior__).

        Returns a ResolvedBinop whose method has a void return type and a cpp
        template that mutates {self} in place (e.g. tpy::set_update({self}, {0})).
        For user-defined methods, generates {self}.__iadd__({0}) call.
        """
        method_name = builtin_modules.AUGOP_TO_IMETHOD.get(op)
        if not method_name:
            return None

        target_effective = self.get_effective_type_for_binop(target_type)
        record = self.ctx.registry.get_record_for_type(target_effective)
        if not record:
            return None

        type_subst = self._build_type_subst(target_effective, value_type)
        value_arg = unwrap_ref_type(value_type)
        home = literal_home(value_type, target_type, target_effective)

        found = self._rank_set(self.dunder_overloads(record, method_name),
                               value_arg, type_subst, target_type,
                               _operand_exprs(loc_node)[0], home)
        if found is None or isinstance(found, OperatorAmbiguity):
            return found
        return self._make_resolved(found, target_effective, loc_node)

    def get_aug_inplace_param_types(
        self, target_type: TpyType, op: str,
    ) -> list[TpyType]:
        """The parameter types of the in-place operator's variants, at the
        target's type args; empty when it defines none.

        Used to produce precise type mismatch errors when resolve_aug_inplace
        fails: "expected X, got Y" (or "expected X or Z") instead of a
        generic "not supported" error.
        """
        method_name = builtin_modules.AUGOP_TO_IMETHOD.get(op)
        if not method_name:
            return []

        target_effective = self.get_effective_type_for_binop(target_type)
        record = self.ctx.registry.get_record_for_type(target_effective)
        if not record:
            return []

        type_subst = _type_entries(
            self._build_type_subst(target_effective, target_effective))
        params = [m.params[0].type
                  for m in self.dunder_overloads(record, method_name)
                  if m.params]
        if type_subst:
            params = [substitute_type_params_simple(p, type_subst)
                      for p in params]
        return params

    def resolve_unaryop(
        self, operand_type: TpyType, op: str, loc_node=None,
    ) -> ResolvedUnaryop | None:
        """Resolve unary operator using registry."""
        method_name = builtin_modules.UNARYOP_TO_METHOD.get(op)
        if not method_name:
            return None

        effective_type = self.get_effective_type_for_binop(operand_type)
        record = self.ctx.registry.get_record_for_type(effective_type)
        if record:
            overloads = self.dunder_overloads(record, method_name)
            if overloads and len(overloads[0].params) == 0:
                method = overloads[0]
                class_subst = _type_entries(
                    self._build_type_subst(effective_type, effective_type))
                self._check_class_bounds(method, effective_type, class_subst, loc_node)
                return ResolvedUnaryop(method=method)

        return None
