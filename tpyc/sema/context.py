"""
TurboPython Semantic Analysis Context

Contains the shared state that is passed to all semantic analysis components.
"""

from __future__ import annotations
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Callable, Iterator, NamedTuple, TYPE_CHECKING

from ..identity_map import IdentityMap, IdentitySet
from ..macro_loader import MacroRegistry
from ..parse.nodes import SourceLocation, TryTier
from .. import qnames
from . import own_copy
from .value_range import ValueRange

if TYPE_CHECKING:
    from ..parse import TpyGeneratorExpression
    from ..parse.type_resolver import TypeResolver
    from .scope_tracker import DeferredEscape

from ..typesys import (
    TpyType, TypeRegistry, ListLiteralInfo, DictLiteralInfo, SetLiteralInfo, ViewVarInfo, TypeParamKind, IntLiteralType,
    INT32, BIGINT, NominalType, ReadonlyType, OwnType, OptionalType, UnionType, TupleType,
    RecursiveAliasInstanceType, recursive_union_alternatives,
    PendingListType, PendingDictType, PendingSetType,
    PendingGenericInstanceType, PendingGenericInstanceInfo,
    ViewTypeFamily, PendingViewType, PendingStrType, VIEW_TYPE_FAMILIES,
    unwrap_readonly, unwrap_ref_type, unwrap_qualifiers,
    FunctionInfo,
    is_dyn_protocol, contains_pending_leaf,
)
from ..namespace import Namespace
from ..type_def_registry import int_traits_of, is_borrowing_view_type
from ..parse import (
    TpyExpr, TpyStmt, TpyRecord, TpyFunction, TpyVarDecl, TpyMethodCall,
    TpyCall, TpyCoerce, TpyName, TpySubscript, TpyFieldAccess, TpyBinOp,
    TpyUnaryOp, TpyIfExpr, TpyTupleLiteral, TpyVarargPack, TpyStarUnpack,
    TpyNestedDef, TpyNamedExpr, TpyIntLiteral, TpyStrLiteral,
    is_parse_node,
    is_property_getter_read,
)
from ..diagnostics import Diagnostic, DiagnosticLevel, SemanticError, Scope
from ..value_category import call_returns_cpp_ref
from .type_ops import signature_may_return_borrow

# Tuple of all pending container types -- use in isinstance checks so adding
# a new container type requires updating only this one constant.
PENDING_CONTAINER_TYPES = (PendingListType, PendingDictType, PendingSetType)


def addr_taken_roots(expr: TpyExpr) -> list[str]:
    """Return all variable names whose storage is potentially aliased by expr.

    Used to mark params as mutated when their address is taken (directly or
    implicitly). Handles or/and (TpyBinOp ||/&&) and ternary (TpyIfExpr),
    returning all possible roots across branches.
    """
    if isinstance(expr, TpyCoerce):
        return addr_taken_roots(expr.expr)
    if isinstance(expr, TpyName):
        return [expr.name]
    if isinstance(expr, TpySubscript):
        return addr_taken_roots(expr.obj)
    if isinstance(expr, TpyFieldAccess):
        return addr_taken_roots(expr.obj)
    if (is_property_getter_read(expr)
            and signature_may_return_borrow(expr.resolved_function_info)):
        # ... and the ACCESSOR spelling of that field read: a getter handing
        # back a reference into its receiver's storage aliases the receiver,
        # so the address taken is the receiver's. Keyed on what the getter's
        # SIGNATURE can return, not on the node kind: a by-value getter hands
        # back a copy and aliases nothing, while an open-`T` one is a
        # reference at a reference instantiation and must be taken as aliasing
        # -- this is an escape question, where the conservative answer is the
        # safe one.
        return addr_taken_roots(expr.obj)
    if isinstance(expr, TpyBinOp) and expr.op in ("||", "&&"):
        return addr_taken_roots(expr.left) + addr_taken_roots(expr.right)
    if isinstance(expr, TpyIfExpr):
        return addr_taken_roots(expr.then_expr) + addr_taken_roots(expr.else_expr)
    return []


def tuple_borrow_escape_roots(expr: 'TpyExpr', tuple_bare: 'TupleType',
                              ro_tuple: bool) -> list[tuple[str, bool]]:
    """(root, grants_write) pairs for a borrow-form tuple escaping through a
    yield/return slot. A tuple literal borrows exactly its pointer-repr
    elements' roots (addr_taken_roots has no tuple-literal case; value
    elements are copied into the slot); a readonly slot or element records
    provenance without granting write access.
    """
    inner = expr.expr if isinstance(expr, TpyCoerce) else expr
    if isinstance(inner, TpyTupleLiteral):
        return [
            (root, not ro_tuple and not isinstance(
                tuple_bare.element_types[i], ReadonlyType))
            for i, el in enumerate(inner.elements)
            if i < len(tuple_bare.element_types)
            and TupleType._element_is_pointer_repr(tuple_bare.element_types[i])
            for root in addr_taken_roots(el)
        ]
    return [(root, not ro_tuple) for root in addr_taken_roots(expr)]


def _storage_key(expr: TpyExpr) -> str | None:
    """Extract storage key: 'name' or 'name.field' for single-level field access.

    Used by the borrow tracker as a dict key to identify storage that can be
    borrowed from or mutated.  Supports dotted paths so that ``self.items``
    and ``obj.field`` are tracked separately from ``self`` / ``obj``.

    An unbound-self access (``Base.field``, the syntactic receiver being the
    ancestor CLASS) keys at ``self``: the storage it names is this object's,
    so a loan filed under the class name would never meet the mutation
    through ``self.field`` that clobbers it.
    """
    if isinstance(expr, TpyName):
        return expr.name
    # A @property read is a getter CALL with no storage of its own, but it is
    # SPELLED as the member it reads through and is keyed like one: the same
    # two arms, over the method name instead of the field name, so a getter
    # and a stored field can never be keyed differently.
    if is_property_getter_read(expr):
        member = expr.method
    elif isinstance(expr, TpyFieldAccess):
        member = expr.field
    else:
        return None
    if expr.unbound_self_parent_type is not None:
        return f"self.{member}"
    if isinstance(expr.obj, TpyName):
        return f"{expr.obj.name}.{member}"
    return None


class IndexRelation(Enum):
    """How the element two subscripts of one container name are related."""
    SAME = "same"          # provably the same element
    DISTINCT = "distinct"  # provably different elements
    UNKNOWN = "unknown"    # may be either


def element_index_key(expr: TpyExpr) -> tuple[str, int | str] | None:
    """A comparable identity for a subscript index, or None when the index
    has none (an arithmetic expression, a slice, a call).

    Two forms answer: a non-negative int or a str LITERAL, whose values
    settle both SAME and DISTINCT, and a bare NAME, whose spelling settles
    SAME only (two different names may still hold one index). A negative int
    literal deliberately has no key: `rows[-1]` and `rows[1]` are the same
    element of a two-element list, so treating them as distinct would be
    unsound.
    """
    while isinstance(expr, TpyCoerce):
        expr = expr.expr
    if isinstance(expr, TpyIntLiteral):
        return ("lit", expr.value) if expr.value >= 0 else None
    if isinstance(expr, TpyStrLiteral):
        return ("lit", expr.value)
    if isinstance(expr, TpyName):
        return ("name", expr.name)
    return None


def element_index_relation(a: 'tuple[str, int | str] | None',
                           b: 'tuple[str, int | str] | None') -> IndexRelation:
    """The relation between two `element_index_key` answers.

    Equal keys are SAME whichever form they take; two literals that differ
    are DISTINCT. Everything else -- a missing key, a literal against a name,
    two different names -- is UNKNOWN, which warns but must say so.
    """
    if a is None or b is None:
        return IndexRelation.UNKNOWN
    if a == b:
        return IndexRelation.SAME
    if a[0] == "lit" and b[0] == "lit":
        return IndexRelation.DISTINCT
    return IndexRelation.UNKNOWN


def field_chain_storage_key(expr: TpyExpr) -> str | None:
    """The dotted storage key of a pure field CHAIN ('o.inner.tag'), or None.

    Every caller is on the MUTATION side -- an alias bind, an argument at a
    mutable parameter, a field write -- and a mutation is spelled at whatever
    depth the program wrote it, while `_storage_key` stops at one hop because
    that is all the borrow tracker's dict needs. Spelling the whole path is
    what lets `mark_view_borrowers_mutated` relate the mutation to a view's
    own key by prefix: `m = i` keys 'i' and so marks the view under 'i.name'.
    A hop that dispatches to `__getattr__` (`hidden_call`) mints a temporary
    the path does not own, so such a chain has no key here. Neither does one
    through a `@property`: that hop is a getter CALL, which this walk stops
    at -- `_storage_key` keys it at ONE hop, which is all a loan needs.

    The BORROW side must not call it: a loan is looked up by the one-hop key,
    so a view registered under a deeper dotted path would sit on a key no
    mutation check ever asks about. A chain too deep to key registers no
    borrow at all, which is what `_borrow_storage_root` answering None means.
    """
    parts: list[str] = []
    while isinstance(expr, TpyFieldAccess):
        if expr.hidden_call is not None:
            return None
        parts.append(expr.field)
        expr = expr.obj
    if not isinstance(expr, TpyName):
        return None
    parts.append(expr.name)
    return ".".join(reversed(parts))


def canonical_storage_key(bt: 'BorrowTracker', key: str) -> str:
    """``key`` re-rooted at the storage its root borrows from.

    One buffer must have ONE key. After ``m = o.mid``, ``m.inner.tag`` and
    ``o.mid.inner.tag`` name the same field, and the prefix match that demotes
    a view sees them as one only when both are spelled from real storage.
    """
    root = _storage_root(key)
    for src in bt.all_storage_through_borrows(root):
        if src != root:
            return src + key[len(root):]
    return key


def _storage_root(key: str) -> str:
    """Strip the field suffix off a dotted storage key ('self.items' -> 'self').

    Inverse of `_storage_key`: callers that need to look up the variable name
    a dotted key was built from (param-name table, loop-var dict) use this.
    """
    dot = key.find(".")
    return key if dot == -1 else key[:dot]


def expr_yields_non_null_ptr(expr: TpyExpr, non_null_vars: set[str]) -> bool:
    """Whether evaluating `expr` produces a Ptr value with known non-null provenance.

    Three independent sources, all unified here so consumers (local-init,
    rebind, future return-site / expression-context uses) don't re-enumerate:

    1. Address-taking coercion (`&{e}` -- the `produces_non_null_ptr` flag on
       the Coercion). Covers `p: Ptr[T] = x` and the inheritance / @dynamic
       upcasts. A chain of coercions is walked: any non-address-taking outer
       coercion (e.g. the trivial `Ptr[T] -> Ptr[readonly[T]]`) is transparent
       and provenance is inherited from the inner expression.
    2. Ptr-returning call. Either the call's return type is itself Ptr-typed
       (`take_ptr(x)`, explicit `Ptr(x)` constructor) or the function carries
       the `@value_ptr_coercion` flag (its Ptr param coerces a T arg via `&`).
    3. Read from a name that earlier analysis already marked non-null.
    """
    while isinstance(expr, TpyCoerce):
        if expr.coercion.produces_non_null_ptr:
            return True
        expr = expr.expr
    if isinstance(expr, TpyCall) and expr.args:
        if expr.call_type is not None and expr.call_type.is_pointer():
            return True
        fi = expr.resolved_function_info
        if fi is not None and fi.value_ptr_coercion:
            return True
    if isinstance(expr, TpyName):
        return expr.name in non_null_vars
    return False


def _borrow_storage_root(expr: TpyExpr) -> str | None:
    """Extract the storage key whose storage is borrowed by this expression.

    Handles simple names (``items``), subscript on names or single-level
    field access (``items[i]``, ``self.items[i]``), and field access on
    names (``obj.field``).  Returns None for deeper nesting or rvalues
    (conservative -- no borrow registered).
    """
    if isinstance(expr, TpyCoerce):
        return _borrow_storage_root(expr.expr)
    if isinstance(expr, TpyName):
        return expr.name
    if isinstance(expr, TpySubscript):
        return _storage_key(expr.obj)
    if isinstance(expr, TpyFieldAccess):
        return _storage_key(expr)
    if is_property_getter_read(expr):
        # Keyed by the shared speller: a getter is one hop off a name like the
        # stored field it reads through, and unkeyable deeper. Dropping to the
        # call's None would let a deep property chain answer "placeable" where
        # the field spelling answers "unplaceable", and the routes that consult
        # the verdict would iterate storage no loan guards.
        return _storage_key(expr)
    return None


def iter_borrow_storage(expr: TpyExpr) -> str | None:
    """The storage an lvalue for-each iterable's ITER borrow is filed under,
    or None when the iterable has no storage the invalidation check can ask
    about.

    One question, one walker: the for-each route in `thir/lower` admits an
    iterable only when this answers, and the ITER registration files the loan
    under what it answers, so the loan always sits on a key a mutating
    receiver can be resolved to. A chain deeper than a loan key can be spelled
    (`self.grid.rows[i]`, `table[k].cells`) answers None rather than falling
    back to the chain root: a loan filed at the root is coarser than anything
    the check looks up, so the iteration would hand out references into
    storage nothing guards.

    It forwards to the shared walker rather than adding one: the walker's
    other callers ask a different question (which storage a local init or an
    argument position borrows), so the contract above would have nowhere to
    be stated if the for-each route called it directly.
    """
    return _borrow_storage_root(expr)


def _borrow_storage_roots(expr: TpyExpr) -> list[str]:
    """Storage keys borrowed by ONE argument position.

    A `*args` pack occupies a single parameter slot while holding many
    operands, and a callee that borrows the pack borrows every packed
    element -- so the slot contributes one root per element instead of the
    None a whole-pack expression would yield. A forwarded pack (`f(*xs)`)
    is the same slot spelled once: the operand is the source pack itself.
    """
    if isinstance(expr, TpyVarargPack):
        roots = []
        for arg in expr.args:
            if isinstance(arg, TpyStarUnpack):
                arg = arg.expr
            root = _borrow_storage_root(arg)
            if root is not None:
                roots.append(root)
        return roots
    root = _borrow_storage_root(expr)
    return [] if root is None else [root]


class CallOperands(NamedTuple):
    """A call-shaped expression as the borrow facts index it: the callee,
    its receiver (source index -1) and its positional arguments."""
    fi: FunctionInfo
    obj: TpyExpr | None
    args: list[TpyExpr]


def call_borrow_operands(expr: TpyExpr) -> CallOperands | None:
    """The operands a call-shaped `expr`'s `return_borrows_from` indexes, or
    None when `expr` is not a resolved call.

    Operator dispatch is a method call in disguise -- a borrow-returning
    dunder hands out a borrow of an operand -- and answers with the CANONICAL
    fi: the resolved copy is synthesized before the dunder's body facts land,
    so only the root carries them.
    """
    if isinstance(expr, TpyCall):
        fi, obj, args = expr.resolved_function_info, None, expr.args
    elif isinstance(expr, TpyMethodCall):
        fi, obj, args = expr.resolved_function_info, expr.obj, expr.args
    elif isinstance(expr, TpyBinOp) and expr.resolved_binop is not None:
        rb = expr.resolved_binop
        fi = rb.method.root
        obj = expr.right if rb.is_reverse else expr.left
        args = [expr.left if rb.is_reverse else expr.right]
    elif isinstance(expr, TpyUnaryOp) and expr.resolved_unaryop is not None:
        fi, obj, args = expr.resolved_unaryop.method.root, expr.operand, []
    else:
        return None
    return None if fi is None else CallOperands(fi, obj, args)


class BorrowKind(Enum):
    """Kind of borrow relationship between a borrower and its storage."""
    ALIAS = "alias"       # whole-container alias (safe through mutations)
    FIELD = "field"       # field-level reference
    ITER = "iter"         # for-loop iterator
    ELEMENT = "element"   # subscript element reference
    PTR = "ptr"           # pointer into storage
    # Possible borrow of unknown shape: the callee's body (and so its
    # return_borrows_from fact) is not analyzed yet, so the bind is assumed
    # to borrow. Gates auto-move of the storage like any borrow, but carries
    # no element/alias semantics -- it must not feed mutation-invalidation
    # warnings or storage-identity (alias) chains.
    OPAQUE = "opaque"


# Restrictiveness order: PTR/ITER/ELEMENT > FIELD/OPAQUE > ALIAS. Used by
# retarget logic to promote a chained borrow to the most-restrictive kind in
# the chain (an ALIAS of an ELEMENT borrower is invalidated by structural
# mutation of the source container) and by FlowFacts borrow merging.
BORROW_KIND_RANK: dict[BorrowKind, int] = {
    BorrowKind.ALIAS: 0,
    BorrowKind.FIELD: 1,
    BorrowKind.OPAQUE: 1,
    BorrowKind.ITER: 2,
    BorrowKind.ELEMENT: 3,
    BorrowKind.PTR: 3,
}

# The kinds a STRUCTURAL mutation (reallocation, insertion, deletion) can
# invalidate: they point INTO the storage, where ALIAS and FIELD name the
# storage itself and survive. OPAQUE is out for its own reason (see its
# definition above): its shape is unknown, so it carries no element or alias
# semantics and must not feed an invalidation warning at all. Spelled once so
# the questions asked of it -- "is anything invalidatable borrowed here"
# (whole-container mutation, slice assignment) and "which element loan does
# this write hit" -- cannot drift apart.
INVALIDATING_BORROW_KINDS: tuple[BorrowKind, ...] = (
    BorrowKind.ITER, BorrowKind.ELEMENT, BorrowKind.PTR)


@dataclass(frozen=True, slots=True)
class LoanInfo:
    """One loan: what shape it has, and whether it is held on an ELEMENT of
    the storage it is filed under rather than on that storage itself. Which
    storage generation it sits in is not tracked here -- `sema.alias_rebind`
    replays the registrations in program order and answers that itself.

    `on_element` is the one hop of the borrowed place the key cannot spell.
    It decides which mutations of the storage clobber the loan: a write into
    a SIBLING element (`rows[j].append(x)`, `rows[j] = ...`) destroys what an
    element loan points into, while a loan on the container itself survives
    it -- `for row in rows: rows[0].append(x)` is sound and must stay quiet.

    `elem_index` is which element, as `element_index_key` spells it. It
    answers the one question the storage key cannot: could the mutated
    element BE the borrowed one. `element_index_relation` decides; a
    DISTINCT answer is no conflict, an UNKNOWN one is a conflict the
    diagnostic must report as possible rather than certain.
    """
    kind: BorrowKind
    on_element: bool = False
    elem_index: tuple[str, int | str] | None = None


def loan_mutation_warning(place: str, cause: str, *, iterating: bool,
                          certain: bool = True) -> str:
    """The text of the borrow-invalidation warning for a mutation of ``place``.

    One speller for every site (a mutating method on a container or on an
    element receiver, element assignment, element and name aug-assign, slice
    and field assignment, `del`), so the branches -- an iteration in progress
    versus any other live borrow -- can only be worded one way. ``cause`` is
    what performed the mutation, already quoted where it is a source spelling
    ("'append'", "'+='", "element assignment").

    ``certain`` is False when the mutated place only MAY be the borrowed one
    (`IndexRelation.UNKNOWN`). The warning still fires -- the program may be
    wrong -- but it may not claim the mutation hits the borrowed element,
    since that is exactly what was not proven. Only the element-hop callers
    pass it, and an element loan reaches them with any kind: the for-each ITER
    registration is the only direct one, but `combine_loan_info` carries
    `on_element` onto a higher-ranked ELEMENT/PTR winner (a rebind of the
    iterated source retargets the loan upstream), so all four wordings are
    live.
    """
    if iterating:
        if certain:
            return (f"Mutation of '{place}' while iterating over it"
                    f" ({cause} invalidates the iterator)")
        return (f"Mutation of '{place}' may hit the element being iterated"
                f" ({cause} may invalidate the iterator)")
    if certain:
        return (f"Mutation of '{place}' while borrowed"
                f" ({cause} may invalidate references)")
    return (f"Mutation of '{place}' may hit a borrowed element"
            f" ({cause} may invalidate references)")


def element_loan_mutation_warning(
        place: str, cause: str,
        hit: 'tuple[LoanInfo, IndexRelation]') -> str:
    """`loan_mutation_warning` worded from an `element_hop_loan` answer.

    Which of the four wordings an element hop takes is a property of the
    loan, not of the mutating statement, so the mapping lives next to the
    speller instead of at each write site: a site that re-derived it could
    claim an iteration for a non-ITER loan, or claim certainty the index
    relation did not prove.
    """
    loan, rel = hit
    return loan_mutation_warning(place, cause,
                                 iterating=loan.kind is BorrowKind.ITER,
                                 certain=rel is IndexRelation.SAME)


# Borrower name of the implicit iterator borrow a for-loop registers. Not a
# real local -- it expires with the statement, not by a rebind of some name --
# so the queries that must not see a synthetic holder name it here rather than
# by shape.
ITER_BORROWER = "__for_iter"


def combine_loan_info(a: LoanInfo, b: LoanInfo) -> LoanInfo:
    """The more dangerous of two loans on the same (storage, borrower) pair,
    by BORROW_KIND_RANK. `a` wins a rank tie, which is what both callers --
    a branch-merge dedup and a retarget onto an existing loan -- want.
    """
    winner = (b if BORROW_KIND_RANK[b.kind] > BORROW_KIND_RANK[a.kind]
              else a)
    # The element hop is a property of the PLACE, not of the kind, so it
    # survives whichever kind wins: one of the two loans points into an
    # element, and the merged loan is clobbered by everything either was.
    # For the same reason the merged loan may name an element only when both
    # legs named the same one; otherwise it stands for two places and must
    # answer with no index at all. So a merged element loan can carry any
    # kind and no index: every consumer of `on_element` must handle an
    # UNKNOWN index relation whatever the kind says.
    on_element = a.on_element or b.on_element
    elem_index = a.elem_index if a.elem_index == b.elem_index else None
    if on_element != winner.on_element or elem_index != winner.elem_index:
        winner = replace(winner, on_element=on_element, elem_index=elem_index)
    return winner


class BorrowTracker:
    """Tracks borrow relationships between variables for mutation safety.

    Manages which variables borrow storage from other variables, enabling
    conflict detection when borrowed storage is mutated.

    Loans live in one record per ``(storage, borrower)`` pair, indexed by
    storage because nearly every query asks "who borrows this name".

    Note: string view source tracking (str_source_borrows) and loop var
    mutation tracking (mutated_loop_vars) remain on SemanticContext since
    they reference string deduction and loop optimization state respectively.
    TODO: consider unifying all borrow-like tracking here if a broader
    "deferred type tracking" system emerges.
    """

    __slots__ = ('loans', 'current_stmt', 'stmt_loans')

    def __init__(self) -> None:
        self.loans: dict[str, dict[str, LoanInfo]] = {}
        # The statement sema is analyzing (set by the statement dispatcher),
        # and every loan registered while it was current, as
        # (storage key, holder, kind) per statement -- what the alias-rebind
        # storage pass replays in program order after the walk.
        self.current_stmt: 'TpyStmt | None' = None
        self.stmt_loans: IdentityMap = IdentityMap()

    def reset(self) -> None:
        """Clear all borrow state (called between function analyses)."""
        self.loans.clear()
        self.current_stmt = None
        self.stmt_loans.clear()

    def add_borrow(self, storage: str, borrower: str, kind: BorrowKind = BorrowKind.ALIAS,
                   *, on_element: bool = False,
                   elem_index: 'tuple[str, int | str] | None' = None) -> None:
        """Record that ``borrower`` borrows from ``storage`` (or, with
        ``on_element``, from the element ``elem_index`` of it).

        The for-each ITER registration is the only site that sets
        ``on_element`` directly, but it is not the only way a loan acquires
        the hop: `combine_loan_info` carries it onto whichever kind wins a
        merge, so an ELEMENT or PTR loan can be on an element too, and with
        no index when the merged legs named different ones.
        """
        self.loans.setdefault(storage, {})[borrower] = LoanInfo(
            kind, on_element, elem_index)
        if self.current_stmt is not None:
            self.stmt_loans.setdefault(self.current_stmt, []).append(
                (storage, borrower, kind))

    def remove_borrower(self, borrower: str) -> None:
        """Remove all borrows held by ``borrower`` (e.g. on reassignment)."""
        to_clean: list[str] = []
        for storage, holders in self.loans.items():
            if holders.pop(borrower, None) is not None and not holders:
                to_clean.append(storage)
        for storage in to_clean:
            del self.loans[storage]

    def rebind_borrower(self, borrower: str, value: TpyExpr | None) -> None:
        """Release old binding loans unless the binding retains itself."""
        if isinstance(value, TpyName) and value.name == borrower:
            return
        self.retarget_storage_borrows(borrower)
        self.remove_borrower(borrower)

    def retarget_storage_borrows(self, storage: str) -> None:
        """Reassignment of ``storage``: retarget its borrowers to the upstream source.

        The reassigned variable is generated as a ``T*`` pointer-local in C++,
        so any borrower like ``view = storage`` aliases whatever ``storage``
        currently points into. After the reassignment we want chains to keep
        tracking the original container, not break silently.

        The retargeted borrow kind is the most-restrictive in the chain
        (PTR/ITER/ELEMENT > FIELD > ALIAS): an ALIAS of an ELEMENT borrower
        is still invalidated by structural mutation of the source container.

        Field-path borrows (``storage.*``) refer to the old object's fields
        and are dropped unconditionally -- the variable will be rebound to a
        different object, so those paths are unreachable.

        If ``storage`` itself was not borrowing anything (no upstream chain),
        its borrowers had nowhere to retarget to and are dropped.
        """
        upstream = self.borrow_source(storage)
        up_loan = self.loans.get(upstream, {}).get(storage) if upstream else None

        holders = self.loans.pop(storage, None)
        if holders and up_loan is not None and upstream is not None:
            for b, child in holders.items():
                # The retargeted loan reaches the upstream object THROUGH the
                # rebound name, so it is at least as clobberable as either leg.
                chained = combine_loan_info(child, up_loan)
                # An OPAQUE borrow has no element/alias shape to promote:
                # rank-promoting it into the invalidating set would mint
                # exactly the mutation warnings the kind exists to avoid.
                if child.kind is BorrowKind.OPAQUE:
                    chained = LoanInfo(BorrowKind.OPAQUE)
                existing = self.loans.get(upstream, {}).get(b)
                if existing is not None:
                    chained = combine_loan_info(existing, chained)
                self.loans.setdefault(upstream, {})[b] = chained

        # Field-path borrows (storage.X) are dropped: the variable rebinds to
        # a different object, so dotted-key borrows are unreachable.
        prefix = storage + "."
        for k in [k for k in self.loans if k.startswith(prefix)]:
            del self.loans[k]

    def has_iter_borrow(self, storage_name: str) -> bool:
        """Check if a variable is borrowed by an active for-loop iterator."""
        return ITER_BORROWER in self.loans.get(storage_name, {})

    def has_element_borrow(self, storage_name: str) -> bool:
        """Check if a variable has element-level or iterator borrows.

        Returns True for borrows that can be invalidated by structural
        mutations (reallocation, insertion, deletion). Returns False for
        whole-container alias borrows which are safe through mutations.
        """
        return any(loan.kind in INVALIDATING_BORROW_KINDS
                   for loan in self.loans.get(storage_name, {}).values())

    def element_hop_loan(
            self, storage: str,
            index: 'tuple[str, int | str] | None' = None
    ) -> 'tuple[LoanInfo, IndexRelation] | None':
        """The invalidating loan held on an element of ``storage`` that the
        element ``index`` may BE, with how sure that is.

        A write into the borrowed element of ``storage`` -- replacing it, or
        calling a reallocating method on it -- destroys what such a loan
        points into. ``index`` is the mutated element's `element_index_key`;
        a DISTINCT relation is no conflict and is skipped, and the returned
        relation (SAME or UNKNOWN) is what the diagnostic must not overstate.
        A loan on ``storage`` ITSELF is not returned; it survives an element
        write, which is why the plain `has_element_borrow` answer cannot
        stand in.
        """
        for loan in self.loans.get(storage, {}).values():
            if loan.on_element and loan.kind in INVALIDATING_BORROW_KINDS:
                rel = element_index_relation(loan.elem_index, index)
                if rel is IndexRelation.DISTINCT:
                    continue
                return loan, rel
        return None

    def has_borrow_of_kinds(self, storage: str, kinds: tuple[BorrowKind, ...]) -> bool:
        """Check if any borrower of storage has one of the given borrow kinds."""
        return any(loan.kind in kinds
                   for loan in self.loans.get(storage, {}).values())

    def has_borrowers_outside(self, storage: str, known_aliases: set[str]) -> bool:
        """Any borrower of ``storage`` (including its dotted field paths)
        not in ``known_aliases``. Bind-based aliases are modeled by
        last-use liveness itself (with dead-alias precision); every other
        borrower -- call-result borrows recorded from return_borrows_from,
        ``__for_iter`` iterator borrows -- is invisible to liveness, so
        moving ``storage`` is unsound while one exists.
        """
        prefix = storage + "."
        for key, holders in self.loans.items():
            if key != storage and not key.startswith(prefix):
                continue
            for b in holders:
                # The iterator borrow is redundant here: an in-body consume of
                # the iterable is kept live by the loop fixpoint in liveness,
                # and a post-loop consume is safe -- gating on it would also
                # block the loop's own consuming-iteration activation.
                if b == ITER_BORROWER:
                    continue
                if b not in known_aliases:
                    return True
        return False

    def effective_storage(self, name: str) -> str:
        """Resolve alias chains to find the underlying storage.

        If ``name`` is an alias of another variable, follows the chain
        (e.g. b -> a -> items) and returns the root storage.  Returns
        ``name`` itself when it is not an alias borrower.
        """
        visited: set[str] = {name}
        current = name
        while True:
            found = None
            for storage, holders in self.loans.items():
                loan = holders.get(current)
                if loan is not None and loan.kind is BorrowKind.ALIAS:
                    found = storage
                    break
            if found is None or found in visited:
                return current
            visited.add(found)
            current = found

    def resolve_obj_storage(self, obj: TpyExpr) -> str | None:
        """The storage key a mutation target's OBJECT is filed under.

        A bare name goes through the alias chain; a one-hop field path is its
        own key, since field paths are never aliased in the tracker. One
        answer for every caller that has to turn a receiver into a loan key --
        a second spelling of it drifts from the alias resolution.
        """
        if isinstance(obj, TpyName):
            return self.effective_storage(obj.name)
        return _storage_key(obj)

    def borrow_kind_of(self, name: str) -> 'BorrowKind | None':
        """Return the kind of borrow that 'name' holds, or None if not a borrower."""
        for holders in self.loans.values():
            loan = holders.get(name)
            if loan is not None:
                return loan.kind
        return None

    def is_deferred_borrow(self, name: str) -> bool:
        """Return True if name ultimately traces back to a deferred ELEMENT borrow.

        A variable is deferred if it is an ELEMENT borrow, or an ALIAS/FIELD borrow
        of a deferred variable (transitively). PTR/ITER borrows are not deferred.
        Termination is guaranteed because borrow chains are acyclic.
        """
        kind = self.borrow_kind_of(name)
        if kind == BorrowKind.ELEMENT:
            return True
        if kind in (BorrowKind.ALIAS, BorrowKind.FIELD):
            source = self.borrow_source(name)
            if source is not None:
                return self.is_deferred_borrow(source)
        return False

    def borrow_source(self, name: str) -> str | None:
        """Return the direct borrow source of name (any borrow kind), or None.

        Unlike effective_storage (ALIAS-only), this finds the container for
        ITER/ELEMENT/FIELD/PTR borrows too -- used to trace loop-var addresses
        back to the source container.
        """
        for storage, holders in self.loans.items():
            if name in holders:
                return storage
        return None

    def all_storage_through_borrows(self, name: str, *,
                                    proven_only: bool = False) -> list[str]:
        """Every ultimate storage `name` can alias, following ALL borrow chains
        (ALIAS + ELEMENT + FIELD + PTR + OPAQUE), nearest chain first.

        Unlike effective_storage (ALIAS-only), this traverses the full chain,
        so a write through an element ref traces back to its source param:
        w ALIAS-borrows v, v ELEMENT-borrows items -> [items].

        A binding usually holds ONE loan and yields one root. A RE-SEATED one
        holds a loan per source (a `match` capture a nested match rebinds, an
        alias reassigned from a second container) and a use of it reaches
        whichever is live, so every consumer that attributes a use back to
        storage needs all of them, not the one that happens to come first.

        A root is a REACHABLE node that has no source of its own -- the
        has-sources test must not be filtered by `visited`, or two loan paths
        converging on one already-expanded node would report the intermediate
        alias as a root and hand an ALL-roots consumer a name that is not
        storage at all. `visited` therefore gates only re-expansion.

        Depth-first in loan-registration order, so the head of the list is the
        nearest chain's root. Empty when `name` borrows nothing, and also when
        every reachable node sits on a cycle (no node without sources) -- both
        leave the answer to the caller's `roots_or_self` fallback.

        `proven_only` leaves OPAQUE loans out: they stand for a callee whose
        borrow fact does not exist yet, so a consumer RECORDING a fact of its
        own from the walk would bake the assumption in for good. Each
        reachable node is expanded once and an expansion scans the edge set,
        so O(V*E) in the per-function borrow graph, iterative (no recursion
        depth tied to chain length).
        """
        visited: set[str] = {name}
        roots: list[str] = []
        stack: list[str] = [name]
        while stack:
            current = stack.pop()
            sources = [storage for storage, holders in self.loans.items()
                       if current in holders
                       and not (proven_only
                                and holders[current].kind is BorrowKind.OPAQUE)]
            if not sources:
                if current != name:
                    roots.append(current)
                continue
            for src in reversed(sources):
                if src not in visited:
                    visited.add(src)
                    stack.append(src)
        return roots

    def storage_roots_or_self(self, name: str) -> list[str]:
        """`all_storage_through_borrows`, falling back to `[name]` when the
        name borrows nothing (or resolves to nothing) -- so a consumer that
        must name SOME storage for every binding gets one answer shape."""
        return self.all_storage_through_borrows(name) or [name]

    def freeze(self) -> frozenset[tuple[str, str, LoanInfo]]:
        """Snapshot loan state as immutable triples for flow analysis."""
        return frozenset(
            (storage, borrower, loan)
            for storage, holders in self.loans.items()
            for borrower, loan in holders.items()
        )

    def restore_from_frozen(
            self, triples: frozenset[tuple[str, str, LoanInfo]]) -> None:
        """Restore loan state from a frozen snapshot."""
        self.loans.clear()
        for storage, borrower, loan in triples:
            self.loans.setdefault(storage, {})[borrower] = loan


class EphemeralKind(Enum):
    """How strongly an ephemeral-borrow name is held.

    HARD: the name borrows a producer step slot at EVERY instantiation, so
    retaining it past the step is rejected outright.
    GATE_ONLY: the name borrows only at the REFERENCE instantiations of an
    open `T` (the step result is a copy at the value ones), so it cannot be
    rejected -- it only feeds the generic-yield slot verdict, which answers
    with a slot choice instead of a diagnostic.
    """
    HARD = "hard"
    GATE_ONLY = "gate_only"


def ephemeral_borrow_root(ephemeral_vars: 'dict[str, EphemeralKind]',
                          expr: 'TpyExpr | None',
                          kind: 'EphemeralKind | None' = None) -> str | None:
    """Return the ephemeral-borrow var name `expr` reads from, else None.

    A bare ephemeral name, a walrus handing one out, or a field/subscript
    chain rooted in one (storing `x.field` retains a borrow into the same
    stale slot). A `.clone()` / copy-producing call breaks the borrow, so
    calls are not roots.

    `kind` restricts the match to names held that strongly; None matches
    either kind (the generic-yield gate's question -- does this source
    borrow at all).
    """
    if isinstance(expr, TpyCoerce):
        return ephemeral_borrow_root(ephemeral_vars, expr.expr, kind)
    if isinstance(expr, TpyNamedExpr):
        return ephemeral_borrow_root(ephemeral_vars, expr.value, kind)
    # A ternary reads from whichever arm is taken -- ephemeral if either is.
    if isinstance(expr, TpyIfExpr):
        return (ephemeral_borrow_root(ephemeral_vars, expr.then_expr, kind)
                or ephemeral_borrow_root(ephemeral_vars, expr.else_expr, kind))
    if isinstance(expr, TpyName):
        held = ephemeral_vars.get(expr.name)
        if held is None or (kind is not None and held is not kind):
            return None
        return expr.name
    if isinstance(expr, (TpyFieldAccess, TpySubscript)):
        return ephemeral_borrow_root(ephemeral_vars, expr.obj, kind)
    return None


def _borrow_kind_of_init(init_unwrapped: TpyExpr) -> BorrowKind:
    """The borrow kind an init shape hands out: a subscript loans the ELEMENT,
    a field access the FIELD, anything else the whole storage (ALIAS)."""
    if isinstance(init_unwrapped, TpySubscript):
        return BorrowKind.ELEMENT
    if isinstance(init_unwrapped, TpyFieldAccess):
        return BorrowKind.FIELD
    return BorrowKind.ALIAS


def _select_arm_loans(expr: TpyIfExpr) -> list[tuple[str, BorrowKind]]:
    """The (storage key, kind) loans a select hands out -- one per borrowing arm.

    A select of lvalue arms stays an lvalue in C++ (`T& r = c ? a[0] : b[0]`,
    or `&elem` into a pointer slot), so the binding aliases WHICHEVER arm is
    taken: both arms are loaned, and arms naming different containers loan
    both of them. An arm with no storage root (a call, a literal, `None`)
    loans nothing. When both arms loan the same root the most-restrictive kind
    wins, since either one can be the live borrow.
    """
    loans: dict[str, BorrowKind] = {}
    for arm in (expr.then_expr, expr.else_expr):
        inner = arm.expr if isinstance(arm, TpyCoerce) else arm
        if isinstance(inner, TpyIfExpr):
            arm_loans = _select_arm_loans(inner)
        else:
            arm_root = _borrow_storage_root(arm)
            arm_loans = ([] if arm_root is None
                         else [(arm_root, _borrow_kind_of_init(inner))])
        for root, kind in arm_loans:
            prev = loans.get(root)
            if prev is None or BORROW_KIND_RANK[kind] > BORROW_KIND_RANK[prev]:
                loans[root] = kind
    return list(loans.items())


def register_binding_borrow(ctx: 'SemanticContext', name: str,
                            init_expr: TpyExpr) -> None:
    """Register `name` as a borrower of `init_expr`'s storage root, with the
    kind and the eager-vs-deferred mutation mark taken from the init shape
    (see `_register_source_borrow`). Shared by the VarDecl and walrus binding
    paths."""
    _register_source_borrow(ctx, name, init_expr, kind=None)


def register_capture_alias_borrow(ctx: 'SemanticContext', name: str,
                                  subject_expr: TpyExpr) -> None:
    """Register a `match`-arm capture as a borrower of the matched storage,
    for mutation ATTRIBUTION only.

    A write through the capture has to climb back to the subject's root, and
    `mark_param_mutated`'s climb reads the borrow graph -- so the loan has to
    be in it, or a mutated param keeps its non-mutating verdict. The kind is
    forced to OPAQUE rather than taken from the subject shape (FIELD/ELEMENT,
    which is what the sibling `register_binding_borrow` picks): the
    dangling-binding hazard of a field/element subject is owned by the
    arm-scoped `_warn_arm_subject_mutation`, so handing the same loan to the
    extent-blind invalidation warnings reports the hazard a second time inside
    the arm and a spurious third time for a legitimate mutation AFTER the
    match, where the binding is dead but the loan is not (docs/IR_DESIGN.md,
    extent-scoped loans).
    """
    _register_source_borrow(ctx, name, subject_expr, kind=BorrowKind.OPAQUE)


def _register_source_borrow(ctx: 'SemanticContext', name: str,
                            source_expr: TpyExpr,
                            kind: BorrowKind | None) -> None:
    """Register `name` as a borrower of `source_expr`'s storage root.

    `kind=None` takes the kind from the init shape (ELEMENT / FIELD / ALIAS)
    and applies the deferral rule below; a caller that passes a kind forces it
    and always defers, because it wants the loan recorded without the init
    shape's invalidation semantics.

    A self-assignment (t = t) aliases nothing new; registering it would put
    a self-edge in the borrow graph. A select registers one loan per arm (see
    `_select_arm_loans`). For the remaining non-simple shapes (deep chains
    like `outer.inner[i]`) there is no root to record, so every address-taken
    root is eagerly marked mutated instead (the binding aliases into them, so
    they must stay `T&`, not `const T&`).

    8a.5: marking the source mutated is DEFERRED until the borrower is
    actually written through for ELEMENT borrows (v = items[i]) and for ALIAS
    borrows (b = y) -- a read-only alias must not force its source mutable. A
    genuine write through the borrower (b.x = 1, or passing b to a mutating
    callee) re-marks the source via mark_param_mutated's full borrow-chain
    follow, so deferral stays sound. FIELD borrows defer only when their root
    traces back to an ELEMENT borrow (checked transitively); PTR/ITER borrows
    and field aliases not rooted at an ELEMENT mark immediately.
    """
    unwrapped = (source_expr.expr if isinstance(source_expr, TpyCoerce)
                 else source_expr)
    bt = ctx.func.borrow_tracker
    if isinstance(unwrapped, TpyIfExpr):
        for arm_root, arm_kind in _select_arm_loans(unwrapped):
            if arm_root != name:
                bt.add_borrow(arm_root, name, kind or arm_kind)
        # The arms stay eagerly marked mutated rather than deferred: a select
        # has no single root that a later write through the borrower could
        # re-mark, so the conservative mark is the only one it gets.
        for alias_root in addr_taken_roots(source_expr):
            ctx.mark_param_mutated(alias_root)
        return
    root = _borrow_storage_root(source_expr)
    if root is None:
        for alias_root in addr_taken_roots(source_expr):
            ctx.mark_param_mutated(alias_root)
        return
    if root == name:
        return
    if kind is not None:
        bt.add_borrow(root, name, kind)
        return
    init_kind = _borrow_kind_of_init(unwrapped)
    bt.add_borrow(root, name, init_kind)
    if not (init_kind in (BorrowKind.ELEMENT, BorrowKind.ALIAS)
            or bt.is_deferred_borrow(root)):
        ctx.mark_param_mutated(root)


def record_stmt_borrow_binding(ctx: 'SemanticContext', name: str,
                               var_type: 'TpyType | None',
                               init_expr: TpyExpr) -> None:
    """Record a statement-level (var-decl / assign / walrus) borrow binding
    of a non-value local, with its const verdict. Codegen's branch pre-decl
    reads the accumulated fact to pick the pointer (alias) form for
    borrow-only names -- materialized here so the form discriminator is
    defined by sema, not re-derived by a codegen body walk."""
    if var_type is None or unwrap_readonly(var_type).is_value_type():
        return
    inner = init_expr
    while isinstance(inner, TpyCoerce):
        inner = inner.expr
    # Binding a second name to a record hands out a second write path to its
    # fields. Whether the alias is ever written through is a whole-body
    # question this bind cannot answer, so a str/bytes view borrowed out of
    # anything under the aliased storage falls back to an owned copy.
    alias_key = field_chain_storage_key(inner)
    if alias_key is not None:
        ctx.mark_all_view_borrowers_mutated(alias_key)
    const = isinstance(ctx.get_expr_type(inner), ReadonlyType)
    if not const and isinstance(inner, TpyMethodCall):
        fi = inner.resolved_function_info
        const = bool(fi is not None and fi.is_readonly
                     and call_returns_cpp_ref(ctx, fi))
    # Operator dispatch mirrors the method-call arm (same rule as
    # _is_const_indirect): a readonly dunder's borrow return binds const,
    # so a branch pre-decl must pick the const pointer form.
    if not const and isinstance(inner, TpyBinOp) and inner.resolved_binop is not None:
        fi = inner.resolved_binop.method
        const = bool(fi.is_readonly and call_returns_cpp_ref(ctx, fi))
    if not const and isinstance(inner, TpyUnaryOp) and inner.resolved_unaryop is not None:
        fi = inner.resolved_unaryop.method
        const = bool(fi.is_readonly and call_returns_cpp_ref(ctx, fi))
    record_borrow_binding(ctx, name, const=const)


def record_borrow_binding(ctx: 'SemanticContext', name: str, *,
                          const: bool) -> None:
    """Record a borrow binding of a non-value local whose const verdict the
    caller already knows.

    For bindings that have no statement-level init expression to inspect -- a
    `with` target borrows `__enter__()`'s result, which is a property of the
    method, not of an expression in the body.
    """
    prev = ctx.func.stmt_borrow_decls.get(name, False)
    ctx.func.stmt_borrow_decls[name] = prev or const


class _ModuleInitSentinel:
    """Sentinel for module-level init context (not a real function, but not None either).

    Truthy so that `if ctx.func.current_function:` passes, but fails
    `isinstance(ctx.func.current_function, TpyFunction)` checks.
    """
    __slots__ = ()
    def __bool__(self) -> bool:
        return True
    def __repr__(self) -> str:
        return "<MODULE_INIT>"


MODULE_INIT_CONTEXT = _ModuleInitSentinel()


def is_body_like_scope(current_function: 'TpyFunction | _ModuleInitSentinel | None') -> bool:
    """True when the current context supports pending-type resolution.

    Real function bodies and ``MODULE_INIT_CONTEXT`` both have a
    ``FunctionTrackingState`` with ``pending_resolutions`` plumbed through
    and ``deduction.resolve_all()`` runs at the end of both (see
    ``analyzer._analyze_top_level``). Class bodies, registration-time
    contexts, and no-context states do not, so empty literals and similar
    late-typed constructs must reject there.
    """
    return isinstance(current_function, TpyFunction) or current_function is MODULE_INIT_CONTEXT


@dataclass
class RecordContext:
    """State for the record currently being analyzed (type params, bounds)."""
    record: TpyRecord | None = None
    type_params: list[str] | None = None
    type_param_kinds: list[TypeParamKind] | None = None
    type_param_bounds: dict[str, TpyType] | None = None


@dataclass(frozen=True, slots=True)
class BindingProvenance:
    """Per-local escape/ownership provenance: one record per local.

    Two merge lattices apply at flow joins (see
    flow_facts.merge_binding_provenance):

      * MUST facts (INTERSECT -- survive only if they hold on every path):
        `safe_to_return` and `param_derived`. Invariant: `param_derived`
        implies `safe_to_return` (safe is the documented superset, so an
        intersecting join that reaches via different safe sources keeps the
        local safe). Consulted by is_dangling_return / is_param_derived_expr.

      * HAZARD facts (UNION -- flagged if they hold on any path), all for
        tuple-typed locals: `owns_fresh_idx` (index of the first element that
        owns fresh non-value storage -- borrowing it across a yield/return
        dangles), `owning_storage` (bound from an owning-tuple call -- a
        borrow-form return would address into the dying local),
        `borrow_into_own_idxs` (plain-borrow elements -- REJECTED at a
        NAME->Own[T] slot) and `copies_into_own_idxs` (owned-by-reference
        elements -- WARNED, the copy-into-owned analog).

    Absent name == default record by construction: callers prune all-default
    records, so a name's membership and its facts stay equivalent under both
    lattices (an absent name contributes the default to every field).
    """

    safe_to_return: bool = False
    param_derived: bool = False
    owns_fresh_idx: int | None = None
    owning_storage: bool = False
    borrow_into_own_idxs: frozenset[int] = frozenset()
    copies_into_own_idxs: frozenset[int] = frozenset()
    # HAZARD (UNION): storage roots a borrow-form tuple local's element
    # pointers alias (terminal roots, pre-expanded at record time). The
    # mark functions trace a yield/return of the bare name through it.
    borrow_source_roots: frozenset[str] = frozenset()


_DEFAULT_PROVENANCE = BindingProvenance()


class DeferredGenericYieldSettle(NamedTuple):
    """One generator parked for the module-end generic-yield-slot verdict.

    `scope` and `ns` are parked beside the state because the body-exit path
    nulls them ON that state: the verdict walk asks whether a yield source is
    rooted in durable storage, and with no scope a local that shadows a module
    global reads as the global and is wrongly certified as durable.
    """

    func: Any
    state: 'FunctionTrackingState'
    scope: Scope | None
    ns: Namespace | None


@dataclass
class LoopClauseEdges:
    """Every edge that leaves one loop's BODY clause, and their verdict.

    A `break` leaves past the `else` and out of the loop, a `continue`
    jumps back to the head and still reaches the `else`, and the body's
    own end falls through to the head as well -- so a name first bound in
    the clause counts as assigned after the loop only when it is bound on
    all of them. `body_assigned` is what the continue and fall-through
    edges join to, taken while the body's state is still live;
    `_finish_loop_clauses` is the one consumer of the whole record.
    """

    breaks: list[frozenset[str]] = field(default_factory=list)
    continues: list[frozenset[str]] = field(default_factory=list)
    body_assigned: frozenset[str] = frozenset()


@dataclass
class FunctionTrackingState:
    """Per-function analysis state.

    Extracted from SemanticContext to keep that class from growing unbounded.
    Reset between functions and saved/restored for nested-def isolation.
    Accessed explicitly by callers via ``ctx.func.<field>``.
    """

    # --- Analysis state (per-function) ---
    current_scope: Scope | None = None
    current_function: TpyFunction | _ModuleInitSentinel | None = None
    # The function or method whose emitted body the code under analysis lands
    # in: itself, or the one a nested def or a genexpr is written in.
    body_root: TpyFunction | None = None
    current_ns: Namespace | None = None
    loop_depth: int = 0
    # One entry per loop whose body is open, innermost last; `break` and
    # `continue` file their snapshots on the innermost. Per-loop, not
    # per-function: a nested def analyzed inside a loop body gets a fresh
    # state (and `loop_depth == 0` with it), so its own `break` cannot
    # reach this loop's record.
    loop_clause_edges: list[LoopClauseEdges] = field(default_factory=list)
    # Enclosing compound statements (if/while/for/with/try/match), outermost
    # first, while their bodies are analyzed. A loop-body-first local is
    # function-scoped in Python, so the binding's stack and the read's are
    # what place its single C++ declaration (see `_pending_decl_anchor`):
    # anchored on the loop itself it lands inside an enclosing block's scope,
    # or before an unrelated sibling loop that then redeclares the name.
    compound_stack: list[TpyStmt] = field(default_factory=list)

    # --- try/except control flow (per-function: a nested def must not
    #     inherit the enclosing function's handler context, or its
    #     @error_return calls pass the must-handle check and emit gotos
    #     to labels outside the lambda) ---
    try_except_error_type: str | None = None
    # Set by the call check when a call's @error_return failure is admitted
    # BECAUSE `try_except_error_type` matches it; the enclosing
    # `_analyze_try_return` reads it back into `TpyTry.handled_error_return`.
    try_except_error_handled: bool = False
    in_except_tier: TryTier | None = None
    # Whether the current except handler has an 'as e' binding (needed for return-tier re-raise)
    in_except_has_binding: bool = False
    # True when analyzing a finally body
    in_finally: bool = False
    # Stack (one frame per finally body being analyzed) of local names a
    # finally-deferred return in the corresponding try borrowed: `del` of
    # such a name inside the finally would free storage the pending return
    # still reads (CPython keeps the object alive via the stashed reference).
    pending_return_borrows: list[frozenset[str]] = field(default_factory=list)

    # --- List/dict/set literal tracking ---
    variable_to_literal: dict[str, int] = field(default_factory=dict)
    pending_resolutions: list[int] = field(default_factory=list)
    # Keyed by the TpyMethodCall; survives `save_function_state`'s deep
    # copy with its keys intact (see `IdentityMap.__deepcopy__`).
    pre_analyzed_method_args: IdentityMap = field(default_factory=IdentityMap)
    variable_to_dict_literal: dict[str, int] = field(default_factory=dict)
    pending_dict_resolutions: list[int] = field(default_factory=list)
    variable_to_set_literal: dict[str, int] = field(default_factory=dict)
    pending_set_resolutions: list[int] = field(default_factory=list)
    # Comprehension/genexpr nodes cache their element/key/value type in a
    # snapshot field (result_elem_type etc.) taken during analysis. When that
    # snapshot is a Pending* container type (a list-literal element), the
    # deferred resolver only updates the element node's type, leaving the
    # snapshot stale -- it must be finalized from the registry after
    # resolve_all. Recorded as (node, attr_name) pairs.
    pending_elem_type_fields: list[tuple[object, str]] = field(default_factory=list)
    # Expression nodes whose cached `expr_types` entry holds a Pending* leaf
    # nested in a composite (`list[Pending]` from a non-array comprehension,
    # `tuple[Pending,...]`). Recorded at set_expr_type so the finalization pass
    # rewrites only these nodes -- never a sweep over the module-wide cache.
    pending_composite_exprs: list[object] = field(default_factory=list)
    # Branch-decl snapshot dicts (the `if_branch_decls` values recorded by
    # this function's branch producers). The snapshots capture binding types
    # BEFORE the deferred container resolution, so resolve_all must finalize
    # each registered map or a Pending* leaf reaches codegen's to_cpp().
    pending_branch_decl_maps: list[dict] = field(default_factory=list)

    # --- Pending generic instance tracking ---
    pending_generic_instances: dict[int, PendingGenericInstanceInfo] = field(default_factory=dict)
    variable_to_generic_instance: dict[str, int] = field(default_factory=dict)

    # --- String local tracking ---
    variable_to_str_var: dict[str, int] = field(default_factory=dict)
    str_source_borrows: dict[str, set[int]] = field(default_factory=dict)
    pending_str_resolutions: list[int] = field(default_factory=list)

    # --- Bytes local tracking ---
    variable_to_bytes_var: dict[str, int] = field(default_factory=dict)
    bytes_source_borrows: dict[str, set[int]] = field(default_factory=dict)
    pending_bytes_resolutions: list[int] = field(default_factory=list)

    # --- Pinned-view alias tracking (StrView/BytesView annotations) ---
    # source_name -> set of pinned-view borrower names. Pending views fall
    # back to the owned type via source_mutated; pinned views can't, so we
    # warn at source reassignment that the view dangles.
    pinned_view_aliases: dict[str, set[str]] = field(default_factory=dict)

    # --- Control flow ---
    super_init_call: TpyMethodCall | None = None
    super_del_call: TpyMethodCall | None = None
    # name -> (type, enclosing-statement stack at the binding, loop-var stmt
    # or None): a loop body's bindings, promoted into scope by the first read
    # after the loop. The stack is what the promotion anchors the C++
    # pre-declaration against -- the read's own stack says how far out the
    # declaration has to go to reach both sites.
    pending_loop_vars: dict[str, tuple[TpyType, tuple[TpyStmt, ...], TpyStmt | None]] = field(default_factory=dict)
    loop_vars: set[str] = field(default_factory=set)
    mutated_loop_vars: set[str] = field(default_factory=set)
    consumed_loop_vars: set[str] = field(default_factory=set)
    deferred_loop_copy_warnings: dict[str, list[int]] = field(default_factory=dict)
    # One loop var can borrow MANY sources: a `*args` pack is a single
    # argument slot holding many operands, so an iterable borrowing the pack
    # borrows every one of them.
    loop_var_iterable: dict[str, list[str]] = field(default_factory=dict)
    # True while analyzing the argument of an explicit copy(...) call --
    # copy-divergence warnings (e.g. dict.get(k, default)) are suppressed,
    # the wrap being the acknowledgment spelling.
    in_copy_call_arg: bool = False

    # --- Scope escape tracking ---
    var_scope_depth: dict[str, int] = field(default_factory=dict)
    hoisted_vars: set[str] = field(default_factory=set)
    # (alias, source) pairs the scope-escape check warned about: the
    # alias-rebind pass owes those no second warning at the source's rebind.
    escape_warned_aliases: set[tuple[str, str]] = field(default_factory=set)
    # Names the scope-escape check moved to a function-scope slot, warned or
    # precautionary. `hoisted_vars` also holds the branch pre-declarations.
    escape_hoisted_vars: set[str] = field(default_factory=set)
    rvalue_vars: set[str] = field(default_factory=set)
    owned_locals: set[str] = field(default_factory=set)
    # Accumulator: all locals that were ever owned. Survives FlowFacts
    # save/restore (not in FlowFacts). Used to compute the exported
    # movable_locals set at function end.
    ever_owned_locals: set[str] = field(default_factory=set)
    # Statement-level borrow bindings (var-decl / assign / walrus):
    # name -> any binding had a readonly/const source. A name bound ONLY
    # this way (never a fresh rvalue, no non-statement binding) takes the
    # pointer (alias) form at branch pre-decls. Accumulator like
    # ever_owned_locals -- survives FlowFacts restores.
    stmt_borrow_decls: dict[str, bool] = field(default_factory=dict)
    # Names bound by non-statement binding kinds (with-as, for-loop var,
    # match capture): their binding machinery owns the storage form, so
    # they are never pointer-form-eligible at branch pre-decls.
    nonstmt_bound_names: set[str] = field(default_factory=set)
    # Names bound by a construct rather than a statement that are nonetheless
    # borrow bindings: an explicit exception to nonstmt_bound_names so the borrow
    # snapshot keeps them pointer-form-eligible regardless of prescan/full-pass
    # ordering. Two members -- a match capture aliasing an lvalue subject, and a
    # `with` target (which borrows `__enter__()`'s result).
    nonstmt_borrow_bindings: set[str] = field(default_factory=set)
    # Accumulator (survives FlowFacts like ever_owned_locals): locals
    # reassigned from a borrow-producing source, so no longer safely movable
    # even if they were owned earlier. Subtracted from the movable set.
    borrow_reassigned_vars: set[str] = field(default_factory=set)
    move_through_vars: set[str] = field(default_factory=set)

    # --- Prescan / last-use ---
    current_reassigned_vars: set[str] = field(default_factory=set)
    # What each binding of a name puts in its storage (`alias_rebind.BindKind`),
    # keyed by the binding node (var-decl, assign, walrus); the alias-rebind
    # storage pass reads it after the walk.
    bind_kinds: IdentityMap = field(default_factory=IdentityMap)
    # Every rvalue rebind of a reference local sema stamped (default OWN);
    # the pass decides them and derives `own_rebind_names`. Identity-keyed
    # so the nested-def state deep copy keeps the real statement objects.
    gate_sites: IdentitySet = field(default_factory=IdentitySet)
    # Locals with a rebind the pass left OWN, for the frame layout's
    # pointer-form verdict; harvested per function by the analyzer.
    own_rebind_names: frozenset[str] = frozenset()
    # Locals whose sole binding is a fresh constructor call of their exact static
    # type (never rebound -- field mutation doesn't count). Their dynamic type is
    # provably their static type, so the polymorphic-slicing guard may move them
    # into an owned poly slot (`Box[P]`) without slicing, exactly like a
    # fresh-rvalue ctor at the store site.
    current_fresh_ctor_locals: set[str] = field(default_factory=set)
    # Fresh str/bytes view tuple-unpack targets, candidates for owned-promotion
    # if they later get hoisted out of a branch (a view would then outlive its
    # source __tup temp / branch-local by-ref source). Consumed at the
    # branch-predecl sites. Per-function; reset with the prescan sets.
    tuple_unpack_view_targets: set[str] = field(default_factory=set)
    current_lvalue_reassigned: set[str] = field(default_factory=set)
    current_aug_assigned_vars: set[str] = field(default_factory=set)
    # alias -> source for simple name-init locals (prescan alias_sources).
    # Consulted when invalidating field facts: a mutation through one name
    # of an alias group invalidates facts rooted at every member.
    current_alias_sources: dict[str, str] = field(default_factory=dict)
    # alias -> root for field/subscript-chain-init locals (prescan
    # chain_alias_sources). Together with current_alias_sources these are
    # the borrowers last-use liveness models itself; the auto-move gate
    # only demotes on borrowers OUTSIDE this set.
    current_chain_alias_sources: dict[str, str] = field(default_factory=dict)

    # --- Definite-assignment tracking ---
    definitely_assigned: set[str] = field(default_factory=set)
    # Assigned, but by a loop body whose local is still pending (not in scope
    # yet, so not spellable in `definitely_assigned`). Same lattice: it merges
    # with `definitely_assigned` at every branch join, so a loop in only one
    # arm leaves the name maybe-unassigned and the promoting read rejects.
    loop_bound_assigned: set[str] = field(default_factory=set)
    init_terminated: bool = False
    narrowed_types: dict[str, TpyType] = field(default_factory=dict)

    # --- Reassignment inference tracking ---
    literal_default_vars: set[str] = field(default_factory=set)
    literal_values: dict[str, list[int]] = field(default_factory=dict)
    unresolved_none_vars: set[str] = field(default_factory=set)
    write_history: dict[str, list[tuple[TpyType, TpyExpr]]] = field(default_factory=dict)
    authoritative_types: dict[str, TpyType] = field(default_factory=dict)
    authoritative_type_lines: dict[str, int] = field(default_factory=dict)
    # Locals whose type was retroactively promoted from the literal-seeded
    # default to a fixed-int target by a typed-slot use (ARG, RETURN, INIT,
    # ASSIGN, SETITEM, FIELD, dict-key). Maps name to the loc of the use
    # that triggered the promotion -- surfaced in later type-mismatch
    # errors when a subsequent use disagrees with the locked type, and
    # consulted by _analyze_assign to refresh stale existing_type when
    # the RHS triggered the promotion of the LHS.
    retro_widened_locs: dict[str, 'SourceLocation | None'] = field(default_factory=dict)

    # --- Global declaration tracking ---
    global_declarations: set[str] = field(default_factory=set)

    # --- Nested def tracking ---
    in_nested_def: bool = False
    nested_def_name: str | None = None
    # Inside a nested-def analysis: the first literal id handed out in it. A
    # pending container literal below it belongs to an ENCLOSING function,
    # whose end -- not this nested def's -- settles its type.
    nested_def_first_literal: int = 0
    # Inside a nested-def analysis: 'self' in outer_scope_locals is the
    # enclosing METHOD's receiver (not an ordinary local named self), so
    # assignment sites can reject rebinds with the receiver message.
    outer_self_is_receiver: bool = False
    outer_scope_locals: set[str] = field(default_factory=set)
    current_nonlocal_names: set[str] = field(default_factory=set)
    # Union of nonlocal targets across all nested defs analyzed so far in
    # this function: any later call may invoke such a closure and rebind
    # these names, so check-elision facts for them die at every call site.
    closure_written_names: set[str] = field(default_factory=set)
    # Captured name -> the nested defs defined SO FAR that capture it. Such a
    # closure reads the enclosing storage at every later call, which the
    # last-use walk loses at a `return` (it clears the live set), so an alias
    # bind of the name must not move it while one of them is still live.
    closure_captured_names: dict[str, set[str]] = field(default_factory=dict)
    nested_def_names: set[str] = field(default_factory=set)
    nested_def_escapes: set[str] = field(default_factory=set)
    nested_def_nodes: dict[str, 'TpyNestedDef'] = field(default_factory=dict)
    # Nested-def names this scope binds, with the `def`'s location, from the
    # prescan -- seeded before the body walk because Python binds them for
    # the whole scope. An entry stays live until something actually binds
    # the name here; while it is live, a read of the name must NOT resolve
    # outward to the shadowed module entity
    # (see check_nested_def_shadowed_read).
    nested_def_pending: dict[str, 'SourceLocation | None'] = field(default_factory=dict)
    # A nested `def` written inside a compound statement's body declares its
    # callable for that block alone, so nothing past the block's end can
    # name it -- even when every path through the block bound it. Live
    # entries are name -> (the enclosing statement, a phrase naming it for
    # diagnostics); `analyze_stmt` moves one into `nested_def_block_dead`
    # when that statement's analysis ends, and a later `def` at the scope's
    # own level takes the name out of both (it declares one that lasts).
    nested_def_block_defs: dict[str, tuple['TpyStmt', str]] = field(default_factory=dict)
    nested_def_block_dead: dict[str, str] = field(default_factory=dict)
    # The namespace level this scope's own bindings land in. `current_ns`
    # walks DOWN into a lambda/comprehension child and UP to the module, so
    # this is the boundary that says whether a name is bound HERE.
    own_ns: 'Namespace | None' = None
    # Mutation marks attempted while analyzing a nested-def body (recorded on
    # the NESTED tracking state, which is otherwise discarded on restore).
    # Entries are (name, through_field, structural). _analyze_nested_def
    # replays the captured/nonlocal/self subset into the enclosing state so
    # closure mutations reach the method's const/param-mutation facts.
    nested_mutation_marks: list[tuple[str, bool, bool]] = field(default_factory=list)

    # --- Per-local escape/ownership provenance (see BindingProvenance) ---
    # One record per local; absent name == default. Mutate only via the
    # bp_* helpers below so all-default records stay pruned (the lattices in
    # flow_facts rely on absent == default).
    binding_provenance: dict[str, BindingProvenance] = field(default_factory=dict)
    # Loop vars / next() results bound from a frame-slot-rooted borrow yield
    # (a generator / genexpr / Iterator[T] source, T non-value): the borrow is
    # valid only until the next iteration step (the generator frame slot is
    # overwritten on each __next__()), so retaining it past the step is unsound.
    # These are kept OUT of the safe-to-return provenance and rejected at escape
    # sites (return / store / container insert / closure capture / yield onward).
    # Active only during the consuming loop body (added before, discarded after).
    # A GATE_ONLY entry carries the same fact for an open-`T` source, which
    # borrows only at its reference instantiations: too weak to reject an
    # escape on, but it feeds the generic-yield slot verdict. ONE table, so
    # alias and walrus propagation reaches both kinds.
    ephemeral_borrow_vars: dict[str, EphemeralKind] = field(default_factory=dict)
    # Borrow-yield rooting checks deferred until `func.generator_locals` is
    # populated: a yielded frame-resident local is a valid borrow root, but the
    # check runs during body analysis, before the frame-local set exists.
    # Entries are (yielded_expr, elem_type, loc).
    pending_yield_root_checks: list[tuple[TpyExpr, TpyType, 'SourceLocation | None']] = field(default_factory=list)
    # Yield sources of a generator whose element is an open `T`, recorded for
    # the per-generator provenance gate (the slot borrows only if EVERY source
    # outlives a suspension). Entries are (yielded_expr, source_is_ephemeral);
    # the ephemeral half must be sampled at the yield, while the loop-body
    # marking is live, and the rooting half is deferred exactly like
    # `pending_yield_root_checks`.
    pending_generic_yield_sources: list[tuple[TpyExpr, bool]] = field(default_factory=list)
    # Return/yield dangling checks over a str/bytes local whose storage the
    # deduction has not settled yet. A use must not be what decides the
    # storage, so the check waits for the post-body drain
    # (`TypeCompatibility.drain_deferred_escape_checks`) and then asks
    # `view_storage_verdict` for the settled answer.
    # Entries are (returned_expr, return_type, loc, source_type, for_yield).
    pending_view_storage_checks: list[
        tuple[TpyExpr, TpyType, 'SourceLocation | None', TpyType, bool]
    ] = field(default_factory=list)
    non_null_ptr_vars: set[str] = field(default_factory=set)
    # Narrowing accumulated during a single statement's expression analysis;
    # flushed into non_null_ptr_vars at the statement boundary. Deferred so
    # sibling accesses within an unspecified-order expression (e.g. `p.x + p.y`,
    # `p.x = p.x + 1`) keep their individual checks -- C++ leaves arithmetic
    # and RHS-vs-LHS sub-expression order unspecified (C++17 only sequences
    # RHS-before-LHS for the whole assignment, not for sub-expressions),
    # so within-statement elision would be unsafe.
    pending_non_null_ptr_vars: set[str] = field(default_factory=set)

    # --- Consumed variable tracking ---
    consumed_vars: set[str] = field(default_factory=set)

    # Owned-erased coroutine locals bound in this body and not read since
    # (any read clears the entry -- every legal read is a consumption or a
    # manual-driving borrow). Survivors warn at body end: a bound coroutine
    # never consumed is destroyed without running.
    unread_coro_locals: dict[str, 'TpyStmt'] = field(default_factory=dict)

    # --- Variable declaration tracking (per-function) ---
    var_decl_by_name: dict[str, 'TpyVarDecl'] = field(default_factory=dict)

    # --- Integer value range tracking ---
    value_ranges: dict[str, 'ValueRange'] = field(default_factory=dict)

    # --- Borrow tracking ---
    borrow_tracker: BorrowTracker = field(default_factory=BorrowTracker)

    # --- Parameter mutation inference ---
    current_param_names: set[str] = field(default_factory=set)
    current_param_name_to_idx: dict[str, int] = field(default_factory=dict)
    current_mutated_param_names: set[str] = field(default_factory=set)
    # Method type params whose `U: T` bound was used representationally in the
    # body (e.g. `Ptr[U] -> Ptr[T]` coercion). Drained to FunctionInfo at body
    # end; codegen reads it at call sites to decide adapter-wrap for structural
    # conformers.
    current_representational_params: set[str] = field(default_factory=set)
    current_rebound_params: set[str] = field(default_factory=set)
    current_call_edges: list = field(default_factory=list)
    current_self_mutated: bool = False
    current_self_struct_mutated: bool = False
    current_struct_mutated_param_names: set[str] = field(default_factory=set)
    current_returned_param_names: set[str] = field(default_factory=set)
    current_consumed_own_params: set[str] = field(default_factory=set)
    # Params whose address has been observed escaping into a mutable Ptr[T]
    # field via `FIELD = PARAM`. Finalized to FunctionInfo.addr_escapes_params
    # at body-analysis end; consumed by param-signature codegen to suppress
    # the `const T&` default so the `&param -> T*` store type-checks.
    current_addr_escape_param_names: set[str] = field(default_factory=set)
    # Awaited sub-frame FunctionInfos for Send/Sync frame classification
    # (sema/frame_traits.py). A None entry is an await whose operand frame sema
    # cannot classify (Task / structural awaitable) -- forces non-Send.
    current_awaited_subframes: list = field(default_factory=list)

    def __deepcopy__(self, memo: dict) -> 'FunctionTrackingState':
        # A snapshot owns the facts, not the structures the whole compilation
        # shares: the parse tree and the registry's function records are not
        # state to roll back, so a save must never clone one. Seeding the
        # memo carries them over by identity while the containers around them
        # still copy deeply -- the same argument `IdentityMap.__deepcopy__`
        # makes for identity KEYS. `LIVE_HANDLE_FIELDS` are shared structure
        # by the same principle, one level up: the enclosing analysis goes on
        # binding into that scope and those namespaces, so the field carries
        # the object itself rather than a copy seeded from its contents.
        new = self.__class__.__new__(self.__class__)
        memo[id(self)] = new
        attrs = vars(self)
        seed_identity_objects(attrs, memo)
        for name, value in attrs.items():
            if name in LIVE_HANDLE_FIELDS:
                setattr(new, name, value)
            else:
                setattr(new, name, deepcopy(value, memo))
        return new

    # --- BindingProvenance accessors ---
    # Reads default-fill from an absent name; writes go through _bp_update,
    # which prunes a record back to absence once every field is default so
    # "absent == default" holds for the flow-merge lattices.

    def _bp_update(self, name: str, **changes: object) -> None:
        cur = self.binding_provenance.get(name, _DEFAULT_PROVENANCE)
        new = replace(cur, **changes)
        if new == _DEFAULT_PROVENANCE:
            self.binding_provenance.pop(name, None)
        else:
            self.binding_provenance[name] = new

    def bp_is_param_derived(self, name: str) -> bool:
        bp = self.binding_provenance.get(name)
        return bp is not None and bp.param_derived

    def bp_is_safe_to_return(self, name: str) -> bool:
        bp = self.binding_provenance.get(name)
        return bp is not None and bp.safe_to_return

    def bp_set_param_derived(self, name: str, value: bool) -> None:
        # param_derived implies safe_to_return (BindingProvenance invariant):
        # raising param also raises safe so the two can't diverge at a setter.
        if value:
            self._bp_update(name, param_derived=True, safe_to_return=True)
        else:
            self._bp_update(name, param_derived=False)

    def bp_set_safe_to_return(self, name: str, value: bool) -> None:
        # Clearing safe must clear param too, or the implication above breaks.
        if value:
            self._bp_update(name, safe_to_return=True)
        else:
            self._bp_update(name, safe_to_return=False, param_derived=False)

    def bp_add_loop_var_provenance(self, name: str) -> None:
        self._bp_update(name, param_derived=True, safe_to_return=True)

    def bp_remove_loop_var_provenance(self, name: str) -> None:
        self._bp_update(name, param_derived=False, safe_to_return=False)

    def bp_owns_fresh_idx(self, name: str) -> int | None:
        bp = self.binding_provenance.get(name)
        return bp.owns_fresh_idx if bp is not None else None

    def bp_is_owning_storage(self, name: str) -> bool:
        bp = self.binding_provenance.get(name)
        return bp is not None and bp.owning_storage

    def bp_borrow_into_own_idxs(self, name: str) -> frozenset[int]:
        bp = self.binding_provenance.get(name)
        return bp.borrow_into_own_idxs if bp is not None else frozenset()

    def bp_copies_into_own_idxs(self, name: str) -> frozenset[int]:
        bp = self.binding_provenance.get(name)
        return bp.copies_into_own_idxs if bp is not None else frozenset()

    def bp_set_tuple_member(
        self,
        name: str,
        *,
        owns_fresh_idx: int | None,
        owning_storage: bool,
        borrow_into_own_idxs: frozenset[int],
        copies_into_own_idxs: frozenset[int],
        borrow_source_roots: frozenset[str],
    ) -> None:
        # All tuple-member hazard fields are (re)derived together per
        # binding -- replacing them atomically preserves the rebind discipline
        # (`t = t` re-installs its own facts) while leaving the return-safety
        # fields, managed separately, untouched.
        self._bp_update(
            name,
            owns_fresh_idx=owns_fresh_idx,
            owning_storage=owning_storage,
            borrow_into_own_idxs=borrow_into_own_idxs,
            copies_into_own_idxs=copies_into_own_idxs,
            borrow_source_roots=borrow_source_roots,
        )

    def bp_borrow_source_roots(self, name: str) -> frozenset[str]:
        bp = self.binding_provenance.get(name)
        return bp.borrow_source_roots if bp is not None else frozenset()


_ATOMIC_VALUES = (str, bytes, bytearray, int, float, complex, bool,
                  type(None), type, Enum)


def _object_members(obj: object) -> Iterator[object]:
    """The values an ordinary object holds, `__slots__` classes included."""
    holder = getattr(obj, '__dict__', None)
    if holder is not None:
        yield from holder.values()
    for cls in type(obj).__mro__:
        for name in getattr(cls, '__slots__', ()):
            try:
                yield getattr(obj, name)
            except AttributeError:
                pass


def seed_identity_objects(value: object, memo: dict,
                          seen: 'set[int] | None' = None) -> None:
    """Map every object under `value` that must NOT be cloned to ITSELF, so a
    `deepcopy` through `memo` hands the original back.

    Two kinds qualify. Parse nodes: the tree the analysis reads, which no
    snapshot owns. Registry `FunctionInfo`s: the call-graph objects Phase-2
    mutation propagation reads facts off BY IDENTITY, reached from a state
    through a call edge. `TpyType`s are skipped -- shared, possibly cyclic,
    and nothing keys on their identity.
    """
    if seen is None:
        seen = set()
    if isinstance(value, _ATOMIC_VALUES):
        return
    key = id(value)
    if key in seen:
        return
    seen.add(key)
    if is_parse_node(value) or isinstance(value, FunctionInfo):
        memo[key] = value
    elif isinstance(value, TpyType):
        pass
    elif isinstance(value, (list, tuple, set, frozenset)):
        for item in value:
            seed_identity_objects(item, memo, seen)
    elif isinstance(value, dict):
        for dict_key, item in value.items():
            seed_identity_objects(dict_key, memo, seen)
            seed_identity_objects(item, memo, seen)
    elif isinstance(value, IdentityMap):
        # Its KEYS survive its own `__deepcopy__`; its values do not.
        for item in value.values():
            seed_identity_objects(item, memo, seen)
    elif isinstance(value, IdentitySet):
        # Its members ARE its keys, so they survive its own `__deepcopy__`.
        pass
    else:
        for member in _object_members(value):
            seed_identity_objects(member, memo, seen)


# FunctionTrackingState fields holding the LIVE handles of the enclosing
# analysis, which a restore has to hand back UNCHANGED -- so
# `FunctionTrackingState.__deepcopy__` carries the objects themselves over,
# not copies of them. A save/restore pair isolates what a nested def or a
# trial WRITES, and neither ever binds into these: both open a child scope and
# a child namespace first, and both install their own function node. A clone
# instead becomes the live object at the restore, so every binding made AFTER
# it lands where nothing else looks -- a local declared after a lambda trial
# never reaches `_collect_generator_locals`' `local_ns`, and its frame slot is
# never emitted -- and every consumer keying on the function NODE by identity
# (the generic-yield settle's parked entry, `_is_frame_resident_local` reading
# `generator_locals`) writes to or reads from a node sema and codegen do not
# hold. `own_ns` belongs here for a further reason: `bound_in_own_scope`
# recognizes the scope's own namespace level by IDENTITY along `current_ns`'s
# parent chain, so a cloned one is never reached and the walk runs on out to
# the enclosing function -- a read of a still-pending nested def then resolves
# outward instead of being rejected.
LIVE_HANDLE_FIELDS = frozenset((
    'current_scope', 'current_ns', 'current_function', 'own_ns',
))


@dataclass
class SemanticContext:
    """Shared state for all semantic analysis components.

    Per-function tracking state is held in ``func`` (FunctionTrackingState).
    Callers access it explicitly as ``ctx.func.<field>`` so the split between
    module-wide state and per-function state is visible at every use site.
    """

    # --- Core ---
    registry: TypeRegistry
    global_scope: Scope
    default_int_type: TpyType = field(default_factory=lambda: INT32)
    macro_registry: MacroRegistry | None = None

    # --- Per-function state ---
    # Accessed explicitly by callers as ``ctx.func.<field>``.
    func: FunctionTrackingState = field(default_factory=FunctionTrackingState)

    # --- Record context ---
    record_ctx: RecordContext = field(default_factory=RecordContext)

    # --- Type cache ---
    expr_types: IdentityMap = field(default_factory=IdentityMap)
    var_types: IdentityMap = field(default_factory=IdentityMap)

    # --- Literal tracking (counters + registries persist across functions) ---
    literal_counter: int = 0
    list_literals: dict[int, ListLiteralInfo] = field(default_factory=dict)
    dict_literals: dict[int, DictLiteralInfo] = field(default_factory=dict)
    set_literals: dict[int, SetLiteralInfo] = field(default_factory=dict)
    pending_generic_counter: int = 0
    str_var_counter: int = 0
    str_vars: dict[int, ViewVarInfo] = field(default_factory=dict)
    bytes_var_counter: int = 0
    bytes_vars: dict[int, ViewVarInfo] = field(default_factory=dict)

    # --- Test annotation facts (persist across functions) ---
    declared_var_types: dict[tuple[int, str], TpyType] = field(default_factory=dict)
    ptr_deref_facts: dict[tuple[int, str], bool] = field(default_factory=dict)
    subscript_bounds_facts: dict[tuple[int, str], bool] = field(default_factory=dict)
    div_zero_facts: dict[tuple[int, str], bool] = field(default_factory=dict)
    cast_safe_facts: dict[tuple[int, str], bool] = field(default_factory=dict)
    # (def line, func name) -> FunctionInfo, for # tpyc: frame_send/frame_sync.
    # Holds the fi, not the answer -- frame traits resolve lazily because
    # awaited sub-frames may belong to bodies analyzed later.
    frame_fact_fns: dict = field(default_factory=dict)

    # --- Import tracking ---
    imports: dict[str, set[tuple[str, str]] | None | str] = field(default_factory=dict)
    # Source line of each user-module import statement, so a diagnostic about
    # an imported NAME can point at the import instead of the whole file
    # (the name tuples in `imports` carry no location of their own).
    user_module_import_lines: dict[str, int] = field(default_factory=dict)
    bare_module_imports: set[str] = field(default_factory=set)
    imported_names: dict[str, tuple[str, str]] = field(default_factory=dict)

    # --- Cross-module support ---
    module_name: str = "__main__"
    # File-derived module name used by codegen for namespaces. Identical to
    # `module_name` for non-entry modules; for the entry point, codegen uses
    # the file name while `module_name` stays "__main__" for runtime
    # `__name__` semantics. Set alongside `module_name` in `bind_imports`;
    # consumed by `_register_union_wrappers` so the wrapper-index origin
    # matches the comparison codegen does against its own `module_name`.
    cpp_module_name: str = "__main__"
    module_cpp_namespace: str | None = None  # from # tpy: cpp_namespace directive
    # The current module's opaque plugin payload (`FrontendModule.macro_data`,
    # `TpyModule.macro_data`). Stashed per-module in `bind_imports` so a
    # `@call_macro` -- which runs during Pass 7 with no module passed to it --
    # can reach it via `CallMacroContext.module_data`. None for parser-produced
    # modules. (Function macros instead receive it as an explicit argument.)
    macro_data: 'Any' = None
    # Reference to the current module's `CompiledModule.exports`. Set by
    # `Compiler._finalize_declarations` so the registration paths can
    # find pre-populated skeleton RecordInfo / FunctionInfo /
    # ProtocolInfo / enum NominalType objects (created by
    # `_pre_populate_decl_exports`) and mutate them in place rather than
    # allocating new ones. Peer modules' analyzer registries, populated
    # by `bind_imports` via `module_info.{records,functions,...}`,
    # capture references to the same skeletons; in-place mutation lets
    # those peer registries see the freshly-finalized data without a
    # post-hoc resync. None when no pre-populated exports are
    # attached (e.g. ad-hoc analyzer construction in tests / REPL).
    module_decl_exports: 'object | None' = None
    # Reference to the current module's per-module attribute table
    # (aliased from `CompiledModule.module_attributes`). Registration
    # paths install bindings here as they mint/adopt records,
    # functions, protocols, enums, variables, type aliases, and
    # imports. None when no compiler-provided table is attached
    # (ad-hoc analyzer construction in tests / REPL), in which case
    # `install_binding` no-ops.
    module_attributes: 'dict | None' = None
    top_level_decls: dict[str, int] = field(default_factory=dict)
    # The module-level statement currently being analyzed at depth 0, set by
    # `_analyze_top_level`'s driver loop. A binding made by THIS statement is
    # the module's namespace-scope storage; one made inside an `if`/`for`/
    # `try` body at module level is a local of the generated `__tpy_init`,
    # with no slot to export, import or borrow from.
    current_module_stmt: 'TpyStmt | None' = None
    # Defining modules this module's code references. Populated by
    # sema.reach_analysis after analysis completes; consumed by codegen
    # to drive transitive include emission.
    reached: set[str] = field(default_factory=set)

    # --- Owning-slot copy verdicts (sema/own_copy.py) ---
    # Every obligation this module's bodies recorded, so the ones no
    # instantiation reached can be dropped once the workspace is analyzed.
    own_copy_obligations: list = field(default_factory=list)
    # Instantiations resolved in this module with a fully concrete
    # substitution -- the roots the discharge walks from.
    own_copy_roots: list = field(default_factory=list)
    _own_copy_seen_roots: IdentityMap = field(default_factory=IdentityMap)
    # Generic callees instantiated with a payload that still names a type
    # param. Both this and `own_copy_obligations` are append-only and sliced
    # per body at `own_copy_mark` / `own_copy_drain`, rather than held on
    # `func`: nested-def analysis deep-copies and then discards that state,
    # which would strand a closure's obligations away from the placeholder
    # they hold.
    own_copy_forwards: list = field(default_factory=list)
    # The spans of both lists a genexpr's function filled while it was analyzed
    # in the middle of an enclosing body: that function drained them already,
    # so the enclosing body's drain leaves them out.
    own_copy_inner_spans: list = field(default_factory=list)

    # Top-level names `register_globals` put in `global_scope` from their
    # ANNOTATION, before any statement was analyzed. Distinct from the
    # scope's membership, which also carries inferred top-level bindings
    # once their init has been analyzed.
    preregistered_globals: set[str] = field(default_factory=set)

    # Module globals some function in this module rebinds through `global`,
    # mapped to that function's name (`global_write_facts`). Decided before
    # any body is analyzed, because a borrow return in the FIRST body has to
    # know about a rebind in the last one.
    rebound_globals: dict[str, str] = field(default_factory=dict)

    # --- Final globals ---
    final_globals: set[str] = field(default_factory=set)
    analyzed_finals: set[str] = field(default_factory=set)

    # --- Builtins ---
    builtin_names: dict[str, TpyType] = field(default_factory=dict)

    # --- Namespace hierarchy (global/builtins persist) ---
    builtins_ns: Namespace | None = None
    macro_ns: Namespace | None = None
    global_ns: Namespace | None = None

    # --- Macro dep modules (populated after macros run) ---
    macro_dep_modules: set[str] = field(default_factory=set)

    # --- Recursive union aliases (self- or mutually-referencing) ---
    recursive_union_names: set[str] = field(default_factory=set)

    # --- Module resolver (parser-owned) ---
    # The `TypeResolver` the parser built for this module.  Wired by
    # `sema.analyzer.analyze()` from `module.resolver`.  Consumed by:
    #   - `_infer_field_type_from_default` to look up same-module
    #     records via the parser's registry (not yet in `ctx.registry`
    #     when the field-default pass runs).
    #   - `register_record`'s macro post-resolve step (resolves
    #     TypeRefNodes in bodies of methods added by class macros,
    #     which join the record after the module-level `resolve_refs`
    #     walk has already finished).
    # No other sema code should touch this; the main parser ->
    # TpyType binding happens via `parse.resolve_refs.resolve_refs`.
    parser_resolver: 'TypeResolver | None' = None

    # --- Control flow (persistent) ---
    in_comprehension: int = 0
    # Nesting depth of CONDITIONALLY EVALUATED operands (a logical RHS, a
    # ternary arm, a chained comparator past the first). An argument built
    # there runs only when the branch is taken -- except where the value
    # cannot move and so cannot be deferred into the branch, which
    # `warn_cond_operand_eager_arg` reports. Zeroed for the duration of a
    # body that carries its own region (`ScopeTracker.deferred_body`).
    cond_operand_depth: int = 0
    is_top_level: bool = False
    # REPL mode: allow @error_return calls at top level (unwrap with panic)
    allow_top_level_error_unwrap: bool = False
    # Re-entry guard for `is_type_nocopy` on a `RecursiveAliasInstanceType`:
    # the wrapper's own self-reference (`list[Tree[T]]` member) would otherwise
    # recurse into Tree[T] forever. Conservative False at the recursion point
    # mirrors `RecursiveAliasInstanceType.is_value_type`'s guard in typesys.
    _evaluating_alias_nocopy: set = field(default_factory=set)
    # Re-entry guard for `is_type_non_copyable`'s record-field walk: a
    # self-/mutually-referential record (`children: list[Node]`) would recurse
    # forever. Conservative False at the recursion point, mirroring the alias
    # guard above. is_type_nocopy needs no such set -- it walks type args only.
    _evaluating_record_noncopyable: set = field(default_factory=set)

    # --- Last-use tracking (shared with codegen, persists across functions) ---
    all_last_uses: IdentitySet = field(default_factory=IdentitySet)
    # The `return <name>` values under a non-suspending finally
    # (liveness.collect_finally_return_candidates; every such return -- the
    # finally can reach the local through aliases/closures, so candidacy is
    # structural, not read-based). Return analysis re-marks eligible
    # reference-type shapes as last-use and stamps
    # TpyReturn.finally_deferred_capture (codegen then materializes the
    # return value after the inline finally chain).
    finally_return_candidates: IdentitySet = field(default_factory=IdentitySet)

    # This module's bodied functions/methods whose body
    # analysis has not run yet -- their return_borrows_from is still None
    # for ordering reasons, not because they cannot borrow. A call-result
    # bind from one of these registers a conservative OPAQUE borrow; once
    # the body is analyzed the fact becomes a frozenset and the set entry
    # is naturally inert (the None check short-circuits first).
    pending_borrow_fact_fis: IdentitySet = field(default_factory=IdentitySet)

    # Per-function states of this module's generic generators whose yield-slot
    # verdict is still owed. The verdict reads `return_borrows_from` of the
    # callees behind its yield sources, and that fact is filled at the END of
    # each callee's body analysis -- so a verdict settled during the
    # generator's own body would answer differently depending on whether the
    # callee happens to sit above or below it. The whole state is kept (not a
    # copy) because the verdict walk asks this function's params, scope and
    # frame locals; nothing mutates it once the body is done. The scope and
    # namespace ride in the record rather than on the state, which the
    # body-exit path nulls.
    deferred_generic_yield_settles: list['DeferredGenericYieldSettle'] = field(default_factory=list)
    # Scope escapes whose diagnostic waits for a callee's borrow fact
    # (`ScopeTracker.settle_deferred_escapes`).
    deferred_escapes: list['DeferredEscape'] = field(default_factory=list)
    # Names the scope-escape check hoisted inside a NESTED def, keyed by its
    # function node: the nested body's own tracking state is discarded at
    # scope exit, so they wait here for the analyzer to harvest them into
    # `function_hoisted_vars` with the enclosing function's results.
    nested_def_hoisted_vars: IdentityMap = field(default_factory=IdentityMap)

    # --- Generator-expression functions ---
    # Set by the analyzer: registers and analyzes a genexpr's function at the
    # expression that creates it, under a function state of its own.
    analyze_genexpr_function: 'Callable[[TpyFunction], None] | None' = None
    genexpr_counter: int = 0
    # genexpr function -> the function or method whose emitted body creates
    # its frame (through any nested def or genexpr between them); None at
    # module level. A table, not a node field: a pointer from the function
    # back up to its creator would make the parse tree cyclic.
    genexpr_roots: 'IdentityMap[TpyFunction, TpyFunction | None]' = field(
        default_factory=IdentityMap)
    # Bodies of the `for` statements whose iterable is being analyzed: a
    # genexpr created there is pulled once per iteration, so those bodies run
    # between its pulls.
    for_head_bodies: 'list[list[TpyStmt]]' = field(default_factory=list)
    # The genexprs created while the current statement is analyzed, each with
    # the captures whose narrowing it took: a statement that KEEPS a lazy value
    # (binds it to a name) lets the genexpr outlive it, which the statement
    # checks once its bound type is known.
    stmt_genexprs: 'list[tuple[TpyGeneratorExpression, tuple[str, ...]]]' = field(default_factory=list)
    # The module's top-level statements while they are analyzed: the body a
    # module-level statement's rebind scan looks through.
    module_top_level_stmts: 'list[TpyStmt] | None' = None

    # --- Consuming method tracking ---
    in_consuming_method: bool = False

    # --- Expression type hint ---
    expr_type_hint: TpyType | None = None

    # --- Branch-declared variable tracking ---
    if_branch_decls: IdentityMap = field(default_factory=IdentityMap)

    # --- Extern symbol tracking ---
    extern_symbols: dict[str, str] = field(default_factory=dict)

    # --- Builder-trace fresh-name counters ---
    # hint -> next index. Shared across BuilderTraceExpander instances
    # so two functions that each open an ArgumentParser trace don't
    # both mint __tpy_builder_argparse_args_1, which would collide as
    # a top-level record/function name in the synthesized module.
    builder_trace_fresh_counters: dict[str, int] = field(default_factory=dict)

    # --- Diagnostics ---
    diagnostics: list[Diagnostic] = field(default_factory=list)

    def is_readonly_name(self, name: str) -> bool:
        """Check if a variable has readonly provenance in its declared scope type.

        isinstance narrowing updates narrowed_types (not scope bindings), so the
        scope binding preserves ReadonlyType even when the expr type is narrowed
        to a concrete member. Reassignment updates the scope binding, so a
        non-readonly reassignment correctly clears this.
        """
        if self.func.current_scope is None:
            return False
        declared = self.func.current_scope.lookup(name)
        return declared is not None and isinstance(declared, ReadonlyType)

    def _resolve_loc(self, node: TpyExpr | TpyStmt | TpyRecord | None) -> SourceLocation | None:
        """Resolve source location from a node, with fallback to current function/record."""
        loc = getattr(node, 'loc', None) if node else None
        if loc is None and isinstance(self.func.current_function, TpyFunction):
            loc = self.func.current_function.loc
        if loc is None and self.record_ctx.record is not None:
            loc = self.record_ctx.record.loc
        return loc

    def error(self, message: str, node: TpyExpr | TpyStmt | None = None) -> SemanticError:
        """Create a SemanticError with location from a node."""
        return SemanticError(message, self._resolve_loc(node))

    def error_from_loc(self, message: str,
                       loc: 'SourceLocation | None') -> SemanticError:
        """Create a SemanticError from a location the caller already has.

        Keeps `error`'s fallback to the enclosing function / record, so a
        diagnostic raised off a recorded line is never less located than one
        raised off a node."""
        return SemanticError(message, loc if loc is not None
                             else self._resolve_loc(None))

    def reject_resumable_nested_def_escape(
            self, name: str, node: 'TpyExpr | TpyStmt | None') -> None:
        """A nested def in an async def / generator is a member function of
        the resumable frame -- it cannot be returned or handed off as a
        value (the callable would dangle once the frame is gone, and a
        member function has no standalone C++ value form). Call it
        directly. No-op in plain sync functions (lambda form escapes fine).
        """
        func = self.func.current_function
        if not isinstance(func, TpyFunction) or not (func.is_async
                                                     or func.is_generator):
            return
        kind = "an async function" if func.is_async else "a generator"
        raise self.error(
            f"nested function '{name}' defined in {kind} cannot escape or "
            f"be passed as a value (it lives on the coroutine frame); call "
            f"it directly", node)

    def emit_error(self, message: str, node: TpyExpr | TpyStmt | None = None) -> None:
        """Record an error diagnostic without raising (allows continued analysis)."""
        self.diagnostics.append(Diagnostic(DiagnosticLevel.ERROR, message, self._resolve_loc(node)))

    def warning(self, message: str, node: TpyExpr | TpyStmt | TpyRecord | None = None) -> None:
        """Record a warning diagnostic (doesn't stop compilation)."""
        self.diagnostics.append(Diagnostic(DiagnosticLevel.WARNING, message, self._resolve_loc(node)))

    def warning_from_loc(self, message: str, loc: 'SourceLocation | None') -> None:
        """Record a warning diagnostic from a SourceLocation."""
        self.diagnostics.append(Diagnostic(DiagnosticLevel.WARNING, message, loc))

    def type_param_bound(self, type_param_name: str) -> 'TpyType | None':
        """The declared bound for a type parameter in the current context.

        The enclosing function's own bounds win over the enclosing record's,
        so a method that shadows a class param reads its own.
        """
        func = self.func.current_function
        if (func is not None and isinstance(func, TpyFunction)
                and type_param_name in func.type_param_bounds):
            return func.type_param_bounds[type_param_name]
        if (self.record_ctx.type_param_bounds
                and type_param_name in self.record_ctx.type_param_bounds):
            return self.record_ctx.type_param_bounds[type_param_name]
        return None

    def _copy_is_unobservable(self, typ: 'TpyType') -> bool:
        """Whether a `ValueType` bound settles the copy question for `typ`.

        True only when the payload's reference-ness is DERIVED from its type
        params and every one of them carries the bound. A payload that is a
        reference type in its own right -- `list[T]`, `Array[T, N]`, a plain
        `class GContainer[T]` -- copies a struct at every instantiation, so
        no bound on `T` silences it; `reference_source_params` returns None
        for exactly those.

        `T: Copyable` deliberately does NOT silence it either: copyable is
        TPy's default, so that bound only says the instantiation is not
        non-copyable (`@nocopy`, or a record with `__del__`) -- it does not
        say the author meant to copy. Only `copy()` at the site says that.

        Every derived param must carry the bound: `tuple[K, V]` with a
        value-typed `K` and an unbounded `V` still copies a `V`.
        """
        params = own_copy.reference_source_params(typ)
        if not params:
            return False
        for name in params:
            bound = self.type_param_bound(name)
            if (not isinstance(bound, NominalType)
                    or bound.qualified_name() != qnames.VALUE_TYPE):
                return False
        return True

    def defer_own_copy_verdict(
        self, display: 'TpyType', target: 'TpyType', dest: str,
        node: 'TpyExpr | TpyStmt | None', kind: str = own_copy.KIND_SLOT,
        hint: str = "",
    ) -> bool:
        """Record an owning-slot copy whose payload is still open.

        Returns False -- the sink answers for itself -- when neither the
        stored value nor the slot names a type parameter. Otherwise the body
        warns HERE, at declaration time, so a library author reads the copy
        contract without instantiating anything; the warning stands whatever
        the instantiations turn out to be, and a non-copyable one (`@nocopy`,
        or a record with `__del__`) later answers the obligation with the
        located error, which `apply_own_copy_verdicts` puts in this
        diagnostic's place.

        Returns True without warning under a `T: ValueType` bound, which
        makes the copy unobservable. `copy()` at the site is the other
        silencer, and it is handled before the sink is reached.
        """
        if not (own_copy.type_has_type_param(target)
                or own_copy.type_has_type_param(display)):
            return False
        # Both sides describe the same copy, so silence needs both to agree
        # -- a side with no type params has no bound to consult and abstains.
        open_sides = [t for t in (target, display)
                      if own_copy.type_has_type_param(t)]
        if all(self._copy_is_unobservable(t) for t in open_sides):
            return True
        diag = Diagnostic(
            DiagnosticLevel.WARNING,
            own_copy.hedge_message(display, dest, kind, hint),
            self._resolve_loc(node))
        self.diagnostics.append(diag)
        obligation = own_copy.OwnCopyObligation(
            display_type=display, target_type=target, dest=dest, kind=kind,
            hint=hint, diag=diag)
        self.own_copy_obligations.append(obligation)
        return True

    def apply_own_copy_verdicts(self, verdicts: 'own_copy.OwnCopyVerdicts') -> None:
        """Compose this module's diagnostics from its hedges and the verdicts.

        A hedge some instantiation answered is replaced, at its position, by
        the verdicts in the order they resolved; a hedge the loop-copy path
        withdrew is no longer in the list and so gets nothing. Runs after
        every module's discharge, since the verdicts come from any module
        that instantiated this one's bodies, and before the duplicate
        collapse, so a body analyzed once per clone still collapses to one
        report.
        """
        promoted: IdentityMap = IdentityMap()
        for obligation in self.own_copy_obligations:
            replacement = verdicts.promoted(obligation)
            if replacement:
                promoted[obligation.diag] = replacement
        if not promoted:
            return
        composed: list[Diagnostic] = []
        for diag in self.diagnostics:
            composed.extend(promoted.get(diag, (diag,)))
        self.diagnostics[:] = composed

    def record_own_copy_instantiation(
        self, callee: object, subst: 'dict[str, TpyType | int]',
        is_record: bool = False,
    ) -> None:
        """Note that `callee` (a FunctionInfo, or a RecordInfo when
        `is_record`) was instantiated under `subst`.

        A substitution that still names a type parameter is the enclosing
        generic body's to forward: it is re-composed when that body is
        itself instantiated. A concrete one is a root the discharge starts
        from.
        """
        if not subst:
            return
        pairs = tuple(
            (name, value) for name, value in subst.items()
            if isinstance(value, TpyType)
        )
        if not pairs:
            return
        edge = own_copy.OwnCopyEdge(callee, pairs, is_record)
        if any(own_copy.type_has_type_param(value) for _, value in pairs):
            self.own_copy_forwards.append(edge)
            return
        try:
            # Dedup per callee IDENTITY -- the outer map owns the callee, so
            # a substituted RecordInfo that dies cannot have its address
            # recycled into a false hit (see tpyc/identity_map.py).
            seen = self._own_copy_seen_roots.setdefault(callee, set())
            key = (pairs, is_record)
            if key in seen:
                return
            seen.add(key)
        except TypeError:
            # An unhashable type arg cannot be deduped; recording the edge
            # twice only costs a repeated (idempotent) discharge.
            pass
        self.own_copy_roots.append(edge)

    def own_copy_mark(self) -> tuple[int, int, int]:
        """Where the body about to be analyzed starts contributing."""
        return (len(self.own_copy_obligations), len(self.own_copy_forwards),
                len(self.own_copy_inner_spans))

    def own_copy_drain(self, mark: tuple[int, int, int]) -> tuple[tuple, tuple]:
        """The obligations and instantiation forwards the body recorded."""
        inner = self.own_copy_inner_spans[mark[2]:]

        def outside(index: int, which: int) -> bool:
            return not any(s[which][0] <= index < s[which][1] for s in inner)
        obligations = tuple(
            o for i, o in enumerate(self.own_copy_obligations[mark[0]:], mark[0])
            if outside(i, 0))
        seen: set = set()
        forwards = []
        for i, edge in enumerate(self.own_copy_forwards[mark[1]:], mark[1]):
            if not outside(i, 1):
                continue
            key = edge.key()
            if key in seen:
                continue
            seen.add(key)
            forwards.append(edge)
        forwards = tuple(forwards)
        return obligations, forwards

    def collapse_duplicate_diagnostics(self) -> None:
        """Drop exact repeats, keeping each diagnostic's first occurrence.

        A body analyzed more than once reports its diagnostics once. Bodies are
        cloned wherever one source construct expands into several ordinary ones
        -- `except (A, B):` into one handler per type, `@auto_readonly` into a
        mutable/const method pair -- so each clone re-analyzes the same lines.

        Collapsing happens here, once analysis is over, rather than in the
        recording methods above: during analysis, callers index into
        `diagnostics` (`compatibility.py` records the position of a loop-copy
        warning for `statements.py` to delete once the loop proves to move
        rather than copy). A recorder that skipped a duplicate would shift
        those positions onto unrelated diagnostics.
        """
        seen: set[tuple[object, ...]] = set()
        kept: list[Diagnostic] = []
        for d in self.diagnostics:
            loc = d.loc
            key = ((d.level, d.message, loc.file, loc.line, loc.column) if loc
                   else (d.level, d.message))
            if key in seen:
                continue
            seen.add(key)
            kept.append(d)
        self.diagnostics[:] = kept

    def nocopy_reason(self, typ: TpyType) -> str:
        """Return a human-readable reason why a type is nocopy.

        For explicitly @nocopy types returns "@nocopy type 'X'".
        For implicitly nocopy types (propagated from fields) returns a message
        explaining which field caused it.
        """
        inner = unwrap_readonly(typ)
        if isinstance(inner, OwnType):
            inner = inner.wrapped
        record = self.registry.get_record_for_type(inner)
        if record is None:
            return f"non-copyable type '{typ}'"
        # Walk fields to find the nocopy one (implicitly propagated)
        for f in record.fields:
            if self.is_type_nocopy(f.type):
                return (
                    f"non-copyable type '{typ}' (field '{f.name}' "
                    f"has non-copyable type '{f.type}')"
                )
        # Check type arguments for generic instantiations
        if isinstance(inner, NominalType) and inner.type_args:
            if record is None or not record.has_copy:
                for arg in inner.type_args:
                    if isinstance(arg, TpyType) and self.is_type_nocopy(arg):
                        return (
                            f"non-copyable type '{typ}' (type argument "
                            f"'{arg}' is non-copyable)"
                        )
        # Check parents
        for p in record.parents:
            parent_rec = self.registry.get_record_for_type(p)
            if parent_rec is not None and parent_rec.is_nocopy:
                return (
                    f"non-copyable type '{typ}' (parent '{p}' "
                    f"is non-copyable)"
                )
        # Explicitly decorated with @nocopy (or builtin nocopy)
        return f"@nocopy type '{typ}'"

    def is_type_nocopy(self, typ: TpyType) -> bool:
        """Check if a type is nocopy, recursively unwrapping wrappers and generics.

        For generic instantiations like Box[NocopyType], checks whether any
        type argument is nocopy. Respects __copy__ escape hatch: if the
        containing record defines __copy__, type-arg nocopy is suppressed.
        """
        if isinstance(typ, ReadonlyType):
            return self.is_type_nocopy(typ.wrapped)
        if isinstance(typ, OwnType):
            return self.is_type_nocopy(typ.wrapped)
        if isinstance(typ, OptionalType):
            return self.is_type_nocopy(typ.inner)
        if isinstance(typ, TupleType):
            for e in typ.element_types:
                if isinstance(e, TpyType) and self.is_type_nocopy(e):
                    return True
            return False
        # Generic recursive alias instance: a @nocopy alternative makes the
        # wrapper's std::variant non-copyable. Mirrors the TupleType walk
        # above; uses the unified alternatives accessor. The recursive
        # alternative (e.g. `list[Tree[T]]`) re-enters Tree[T] -- conservative
        # False at the recursion point matches `is_value_type`'s guard.
        if isinstance(typ, RecursiveAliasInstanceType):
            key = (typ.qname, typ.type_args)
            if key in self._evaluating_alias_nocopy:
                return False
            self._evaluating_alias_nocopy.add(key)
            try:
                for m in (recursive_union_alternatives(typ) or ()):
                    if isinstance(m, TpyType) and self.is_type_nocopy(m):
                        return True
                return False
            finally:
                self._evaluating_alias_nocopy.discard(key)
        record = self.registry.get_record_for_type(typ)
        if record is not None and record.is_nocopy:
            return True
        if isinstance(typ, NominalType) and typ.type_args:
            if record is not None and record.has_copy:
                return False
            for arg in typ.type_args:
                if isinstance(arg, TpyType) and self.is_type_nocopy(arg):
                    return True
        return False

    def is_type_non_copyable(self, typ: TpyType) -> bool:
        """Check if a type cannot be copied at C++ level.

        Superset of is_type_nocopy: also counts records with __del__ (which
        deletes copy ops in the generated struct), and walks the parent
        chain for @nocopy or __del__ (which implicitly deletes copy in
        the child). Propagates through record fields and type args.
        Respects __copy__ escape hatch.

        Used to upgrade "copies X into field/container" warnings to errors --
        the generated C++ would otherwise hit a deleted copy ctor.

        Kept separate from is_type_nocopy because the latter feeds into
        analyzer-level `is_nocopy` propagation and user-facing "@nocopy"
        diagnostics, where the narrower "user declared intent" meaning is
        wanted. This helper is strictly about "will C++ reject the copy?".
        """
        if isinstance(typ, ReadonlyType):
            return self.is_type_non_copyable(typ.wrapped)
        if isinstance(typ, OwnType):
            return self.is_type_non_copyable(typ.wrapped)
        if isinstance(typ, OptionalType):
            return self.is_type_non_copyable(typ.inner)
        if isinstance(typ, TupleType):
            for e in typ.element_types:
                if isinstance(e, TpyType) and self.is_type_non_copyable(e):
                    return True
            return False
        # Mirrors the is_type_nocopy branch: a non-copyable alternative
        # propagates through the wrapper's std::variant. Re-entry returns
        # False to match the value/nocopy recursion guards (the recursive
        # alternative re-enters the same instance).
        if isinstance(typ, RecursiveAliasInstanceType):
            key = (typ.qname, typ.type_args)
            if key in self._evaluating_alias_nocopy:
                return False
            self._evaluating_alias_nocopy.add(key)
            try:
                for m in (recursive_union_alternatives(typ) or ()):
                    if isinstance(m, TpyType) and self.is_type_non_copyable(m):
                        return True
                return False
            finally:
                self._evaluating_alias_nocopy.discard(key)
        # Abstract @dynamic protocol bases have pure virtuals and the
        # concrete size depends on the dynamic type, so they have no usable
        # copy/move ctor at the C++ level.
        if is_dyn_protocol(typ):
            return True
        record = self.registry.get_record_for_type(typ)
        if record is None:
            return False
        if record.has_copy:
            return False
        if record.is_nocopy or record.has_del:
            return True
        # Re-entry guard for self-/mutually-referential records: a field or type
        # arg whose type cycles back here (e.g. a tree node `children: list[Node]`)
        # would otherwise recurse forever through the walks below. Conservative
        # False at the recursion point matches the recursive-alias guard above and
        # is_value_type's -- a cycle alone never deletes copy; the answer is
        # decided by the non-cyclic fields/parents the first entry still walks.
        if typ in self._evaluating_record_noncopyable:
            return False
        self._evaluating_record_noncopyable.add(typ)
        try:
            # Inheritance: recurse into each direct parent so each parent's own
            # logic (including its has_copy barrier up its chain) applies
            # independently. For multi-base, any direct parent being non-copyable
            # makes the child non-copyable; for single-parent, parent.has_copy
            # correctly short-circuits the parent's recursion, matching the old
            # `break`-on-has_copy behavior.
            for p in record.parents:
                if self.is_type_non_copyable(p):
                    return True
            if isinstance(typ, NominalType) and typ.type_args:
                for arg in typ.type_args:
                    if isinstance(arg, TpyType) and self.is_type_non_copyable(arg):
                        return True
            for f in record.fields:
                if self.is_type_non_copyable(f.type):
                    return True
            return False
        finally:
            self._evaluating_record_noncopyable.discard(typ)

    def local_decl_type(self, name: str) -> TpyType | None:
        """The local's DECLARED slot type -- what the slot may hold, which a
        later rebind does not change.

        `var_types` carries the deduction and OwnType overrides stamped on the
        decl node (a pending literal resolved after the fact lands there), so
        it wins over the raw annotation. Returns None for a name with no
        declaration of its own -- a parameter, a loop variable, a global.
        Every consumer that asks what a local's slot holds reads this: the
        namespace binding answers the narrower "what was last stored".
        """
        decl = self.func.var_decl_by_name.get(name)
        if decl is None:
            return None
        t = self.var_types.get(decl)
        if t is None and isinstance(decl.type, TpyType):
            t = decl.type
        return t

    def get_expr_type(self, expr: TpyExpr) -> TpyType | None:
        """Get the cached type of an expression, stripping Ref and Own.

        Ref/Own are internal annotations for reference provenance and
        ownership; callers that need bare types (codegen, type resolution,
        narrowing) should not see them.  ReadonlyType is preserved because
        codegen needs it for const emission.  The assignment handler uses
        the analyze_expr return value directly (which preserves Ref/Own)
        for copy-warning detection.
        """
        typ = self.expr_types.get(expr)
        if typ is None:
            return None
        typ = unwrap_ref_type(typ)
        if isinstance(typ, OwnType):
            typ = typ.wrapped
        return typ

    def get_raw_expr_type(self, expr: TpyExpr) -> TpyType | None:
        """Get the cached type of an expression WITHOUT stripping qualifiers.

        Used by copy-warning detection to see Ref/Own qualifiers.
        """
        return self.expr_types.get(expr)

    def set_expr_type(self, expr: TpyExpr, typ: TpyType) -> None:
        """Cache the type of an expression."""
        self.expr_types[expr] = typ
        # A COMPOSITE carrying a Pending* leaf (a non-array comprehension's
        # `list[Pending]`, a tuple of list literals) is read straight off this
        # cache by the var-decl codegen fallback; record it so resolve_all can
        # finalize just this node rather than sweep the module-wide cache.
        # CROSS-PASS INVARIANT: a BARE Pending* literal is excluded here ONLY
        # because it is a tracked literal whose own cache entry is rewritten to
        # the resolved type by _apply_container_resolution (which runs before
        # _finalize_pending_in_bindings). If a future path ever caches a bare
        # Pending* for a node that does NOT flow through that resolution, this
        # exclusion would silently skip it and reintroduce the codegen crash --
        # the recorded-composite completeness check in
        # _finalize_pending_in_bindings guards the composite half of that.
        # contains_pending_leaf fast-returns on leaves.
        if not isinstance(typ, PENDING_CONTAINER_TYPES) and contains_pending_leaf(typ):
            self.func.pending_composite_exprs.append(expr)

    def record_branch_decls(self, stmt: TpyStmt, mapping: dict) -> dict:
        """Store a branch-decl snapshot for `stmt`, registered for the
        pending-container finalization in resolve_all (see
        pending_branch_decl_maps). Incremental callers keep mutating the
        one registered dict."""
        for typ in mapping.values():
            if typ is not None:
                self._force_branch_decl_lists(typ)
        existing = self.if_branch_decls.get(stmt)
        if existing is not None:
            existing.update(mapping)
            return existing
        self.if_branch_decls[stmt] = mapping
        self.func.pending_branch_decl_maps.append(mapping)
        return mapping

    def _force_branch_decl_lists(self, typ: TpyType) -> None:
        """Force pending list literals reaching a branch-decl slot off the
        Array optimization: sibling branches may bind literals of different
        sizes into the one pre-declared slot (the snapshot sees only one
        literal, and a handler/arm bind is not a visible reassignment), so
        only the plain list form is size-safe."""
        if isinstance(typ, PendingListType):
            info = self.list_literals.get(typ.literal_id)
            if info is not None:
                info.is_mutated = True
            # inner_types() does not traverse a Pending's element type, and
            # a nested literal ([[1, 2]] vs [[3]]) has the same shared-slot
            # size hazard one level down.
            self._force_branch_decl_lists(typ.element_type)
        for inner in typ.inner_types():
            self._force_branch_decl_lists(inner)

    def default_int_for_literal(
        self,
        typ: TpyType,
        warn_node: TpyExpr | TpyStmt | None = None,
    ) -> TpyType:
        """Resolve configured default-int, with range-safe fallback for literals."""
        if not isinstance(typ, IntLiteralType):
            return self.default_int_type
        default_tr = int_traits_of(self.default_int_type)
        if (
            default_tr is not None
            and typ.value is not None
            and not (default_tr.min_value <= typ.value <= default_tr.max_value)
        ):
            if warn_node is not None:
                self.warning(
                    f"Integer literal {typ.value} is outside default {self.default_int_type} range; "
                    "inferring int (BigInt).",
                    warn_node,
                )
            return BIGINT
        return self.default_int_type

    def reset_function_tracking(self) -> None:
        """Reset all per-function tracking state."""
        self.func = FunctionTrackingState()

    def save_function_state(self) -> FunctionTrackingState:
        """Snapshot per-function state (for nested-def and trial isolation).

        The restore installs the SNAPSHOT, so anything the enclosing analysis
        goes on using must survive the round trip as ITSELF. Two groups do:
        the live scope, namespaces and function node (`LIVE_HANDLE_FIELDS`),
        carried over unchanged; and every parse node and registry
        `FunctionInfo` the copy reaches, wherever it sits -- an identity KEY
        (`IdentityMap` / `IdentitySet`, e.g. `pre_analyzed_method_args`), a
        container value, or an attribute of a state object. Each
        `__deepcopy__` says why. Callers get the live handles back from
        `restore_function_state` and must not re-attach them themselves.
        """
        return deepcopy(self.func)

    def restore_function_state(self, saved: FunctionTrackingState) -> None:
        """Restore per-function state from a snapshot."""
        self.func = saved

    @contextmanager
    def trial_scope(self) -> Iterator[None]:
        """Snapshot/restore for tentative semantic analysis (e.g. per-candidate
        lambda body trial under Regime C overload resolution).

        Wraps a body of analysis work whose state changes must be rolled back
        unconditionally on exit -- both on success and on exception.

        Snapshotted surfaces:

        - Per-function state (FunctionTrackingState) -- includes call_edges,
          mutated/struct/addr-escape/returned param sets, self-mutation flags,
          borrow tracker, definitely_assigned, narrowed_types, etc. Deep-copied
          since FunctionTrackingState contains nested mutable containers. The
          live scope, namespaces and function node are NOT part of the rollback
          -- `save_function_state` hands them back as themselves; the trial
          binds only into the lambda's own child scope/namespace.
        - Module-level type cache (``expr_types``) and the literal/view
          counters and registries (``literal_counter``, ``list_literals``,
          ``dict_literals``, ``set_literals``, ``pending_generic_counter``,
          ``str_var_counter``, ``str_vars``, ``bytes_var_counter``,
          ``bytes_vars``).
        - The diagnostics list -- truncated to its pre-trial length so
          warnings/errors emitted during a rejected trial don't leak into
          the user-visible output.

        Lambda AST mutations (``inferred_param_types``, ``inferred_return_type``,
        ``captured_names``, ``captures_by_value``) are the caller's
        responsibility -- save and restore them around the trial. They are
        not part of SemanticContext state.
        """
        saved_func = self.save_function_state()
        saved_expr_types = self.expr_types.copy()
        saved_literal_counter = self.literal_counter
        saved_list_literals = dict(self.list_literals)
        saved_dict_literals = dict(self.dict_literals)
        saved_set_literals = dict(self.set_literals)
        saved_pending_generic_counter = self.pending_generic_counter
        saved_str_var_counter = self.str_var_counter
        saved_str_vars = dict(self.str_vars)
        saved_bytes_var_counter = self.bytes_var_counter
        saved_bytes_vars = dict(self.bytes_vars)
        saved_diagnostics_len = len(self.diagnostics)
        try:
            yield
        finally:
            self.restore_function_state(saved_func)
            self.expr_types.clear()
            self.expr_types.update(saved_expr_types)
            self.literal_counter = saved_literal_counter
            self.list_literals.clear()
            self.list_literals.update(saved_list_literals)
            self.dict_literals.clear()
            self.dict_literals.update(saved_dict_literals)
            self.set_literals.clear()
            self.set_literals.update(saved_set_literals)
            self.pending_generic_counter = saved_pending_generic_counter
            self.str_var_counter = saved_str_var_counter
            self.str_vars.clear()
            self.str_vars.update(saved_str_vars)
            self.bytes_var_counter = saved_bytes_var_counter
            self.bytes_vars.clear()
            self.bytes_vars.update(saved_bytes_vars)
            del self.diagnostics[saved_diagnostics_len:]

    def mark_loop_var_mutated(self, name: str) -> None:
        """Mark a for-each loop variable as mutated (prevents const-ref binding)."""
        if name in self.func.loop_vars:
            self.func.mutated_loop_vars.add(name)

    def mark_loop_var_consumed(self, name: str) -> None:
        """Mark a for-each loop variable as consumed (copied into owned storage).

        This triggers auto-consuming iteration when the container is at last
        use, so elements are moved instead of copied.
        """
        if name in self.func.loop_vars:
            self.func.consumed_loop_vars.add(name)

    def own_scope_binding(self, name: str) -> tuple[bool, bool]:
        """`(bound, reaches)` for `name` against THIS function scope, from
        ONE namespace walk.

        `bound`: the scope binds the name -- its params, its locals, or a
        sub-scope of it (lambda param, comprehension variable, live
        `except ... as` / match capture). An enclosing function's or the
        module's binding of the same name is NOT this scope's, which is the
        whole point: Python resolves such a name against this scope alone
        once this scope binds it anywhere.

        `reaches`: a binding of it reaches this read. A SUB-scope binding is
        that scope's own and always reaches -- the enclosing scope's flow
        says nothing about it. A binding at this scope's OWN level is the
        definite-assignment question, and `definitely_assigned` is the fact
        that answers it: the namespace entry a binding inside an `if` /
        `for` / `while` / `try` / `with` / `match` body makes is never
        withdrawn, but the branch merges drop the name from the assigned
        set, which is exactly the path on which Python raises
        UnboundLocalError."""
        ns = self.func.current_ns
        root = self.func.own_ns
        if ns is None or root is None:
            assigned = name in self.func.definitely_assigned
            return assigned, assigned
        while ns is not None and ns is not self.global_ns:
            if ns.has_local(name):
                if ns is not root:
                    return True, True
                return True, name in self.func.definitely_assigned
            if ns is root:
                break
            ns = ns.parent
        return False, False

    def check_nested_def_shadowed_read(
            self, name: str, node: 'TpyExpr | TpyStmt | None') -> None:
        """Reject a read of `name` that Python resolves to a nested `def` of
        this scope but sema would resolve outward.

        Python binds a nested def's name for the WHOLE enclosing scope, while
        sema binds the FunctionInfo only when the walk reaches the `def`;
        without this, an earlier read silently reaches the shadowed module
        function / class / enum.

        `own_scope_binding` answers two of the three facts this needs.
        Ownership is asked, rather than `definitely_assigned`, because an
        `except ... as name` handler leaves the name in the assigned set
        after unbinding it, and the read that follows is exactly the one
        that must not resolve outward. Reachability is where a `def` inside
        an `if` / loop / `try` / `with` / `match` body differs: the name is
        this scope's local everywhere, but only the paths through the `def`
        bind it, so the others are CPython's UnboundLocalError.

        The third is `nested_def_block_dead`: such a `def` declares its
        callable for its block only, so a read past the block's end has
        nothing to reach even on the paths where the binding is definite --
        the whole-scope local Python gives the name has no counterpart
        here, and there is no outer `name` to fall back to."""
        # This runs on every bare-name read and every bare-name call, so the
        # two dict lookups that can possibly fire come before the namespace
        # walk. A nested def's own name is deliberately not seeded into
        # `nested_def_pending`, so the recursion leg needs its own test.
        recursive = (self.func.in_nested_def
                     and name == self.func.nested_def_name)
        if not recursive and name not in self.func.nested_def_pending:
            return
        bound_here, reaches = self.own_scope_binding(name)
        block = (None if recursive
                 else self.func.nested_def_block_dead.get(name))
        # The recursion leg is a restriction on the emitted lambda, not a
        # definite-assignment question, so a binding of the name is enough
        # there; the ordinary "may not be assigned" check covers the rest.
        if bound_here and (recursive or (reaches and block is None)):
            return
        # A `global` / `nonlocal` declaration takes the name out of this
        # scope's binding set entirely, so nothing here shadows anything.
        if (name in self.func.global_declarations
                or name in self.func.current_nonlocal_names):
            return
        if recursive:
            raise self.error(
                f"Recursive nested functions are not supported. "
                f"'{name}' cannot call itself", node)
        loc = self.func.nested_def_pending[name]
        where = f" on line {loc.line}" if loc is not None else ""
        if block is not None:
            raise self.error(
                f"'{name}' is not readable after {block}: the nested "
                f"function '{name}' defined{where} is bound inside that "
                f"block and does not outlive it, and a nested 'def' makes "
                f"its name a local of the whole enclosing scope, so this "
                f"read cannot reach an outer '{name}' -- move the 'def' "
                f"above the block, or rename it",
                node)
        # Which of the two remaining ways the binding fails to reach this
        # read is a source-order question, not a scope one: a `def` the walk
        # has already passed bound the name on SOME path only (the other arm
        # of the same branch), while one still ahead has bound it on none.
        read_loc = getattr(node, "loc", None)
        if (loc is not None and read_loc is not None
                and loc.line <= read_loc.line):
            raise self.error(
                f"'{name}' may not be assigned at this point: the nested "
                f"function '{name}' defined{where} binds it only on some "
                f"paths, and a nested 'def' makes its name a local of the "
                f"whole enclosing scope, so this read cannot reach an outer "
                f"'{name}' -- move the 'def' above the block, or rename it",
                node)
        raise self.error(
            f"'{name}' is read before the nested function '{name}' defined"
            f"{where} is bound. A nested 'def' makes its name a local of the "
            f"whole enclosing scope, so this read cannot reach an outer "
            f"'{name}' -- move the 'def' above this line, or rename it",
            node)

    def receiver_self_in_scope(self) -> bool:
        """True when 'self' in the current function scope is the method
        receiver (a closure captures it as the C++ this pointer -- an
        alias), not an ordinary local/param that happens to be named self."""
        f = self.func.current_function
        return (isinstance(f, TpyFunction) and f.is_method
                and not f.is_staticmethod)

    def is_module_slot_stmt(self, stmt: 'TpyStmt') -> bool:
        """True when `stmt` is the module-level statement whose bindings get
        namespace-scope storage: a direct element of the module's statement
        list, not one nested in a block body and not a synthetic init temp."""
        return self.is_top_level and stmt is self.current_module_stmt

    def define_module_global(self, name: str, var_type: 'TpyType | None',
                             line: int) -> None:
        """Record a module-slot binding. The single writer of the module's
        global tables, so the export collection, the storage-durability
        checks and the order-aware codegen all see one verdict.

        `top_level_decls` keeps the EARLIEST line, so a use between two
        re-declarations still resolves against the global."""
        self.global_scope.define(name, var_type)
        prior = self.top_level_decls.get(name)
        self.top_level_decls[name] = line if prior is None else min(prior, line)

    def mark_param_mutated(self, name: str, *, through_field: bool = False) -> None:
        """Mark a function parameter as directly mutated (Phase 1 of mutation inference).

        For 'self': sets current_self_mutated (method self-mutation tracking).
        For regular params: adds to current_mutated_param_names.
        Also traces loop variables back to their source iterables, and traces
        element/field/ptr borrows back to their source storage (8a.5: deferred
        marking for element refs -- when v = items[i] and v.field is written,
        the write propagates back to items).

        ``through_field``: the mutation is a genuine through-reference write
        (field/subscript/slice assignment, del) rather than the speculative
        non-readonly-method-call mark. Only then do we climb a field-path
        borrow root (`o.items` -> `o`) to the owning param: a method call whose
        callee turns out readonly must NOT demote the receiver, so the
        method-call mark stops at the field-path key as it always has.
        """
        if self.func.in_nested_def:
            self.func.nested_mutation_marks.append((name, through_field, False))
        if name == "self":
            self.func.current_self_mutated = True
            return
        if name in self.func.current_param_names and name not in self.func.current_rebound_params:
            self.func.current_mutated_param_names.add(name)
        for iterable in self.func.loop_var_iterable.get(name, ()):
            # Field-path iterables ("c.items") need root extraction for param lookup
            self.mark_param_mutated(_storage_root(iterable), through_field=through_field)
        for src in self.func.bp_borrow_source_roots(name):
            self.mark_param_mutated(src, through_field=through_field)
        # 8a.5: trace through element/field/ptr borrows to source param.
        # When v = items[i] (deferred) and v is later written through,
        # mark the ultimate storage root (e.g. items) as mutated.
        # EVERY reachable root, not just the first: a re-seated binding holds
        # one loan per source and the write reaches whichever is live.
        # The recursive call terminates because a root has no upstream borrow
        # pointing to it, so it yields no further sources.
        for ultimate in self.func.borrow_tracker.all_storage_through_borrows(name):
            self.mark_param_mutated(ultimate, through_field=through_field)
        # A borrow rooted at a field path (`o.items`, registered for `e = o.items[0]`
        # or for an @auto_readonly accessor result `x = o.b.get()`) reaches the
        # owning param through that field; a genuine write through the borrower
        # mutates the param. The leaf-name walk in _root_name_of_expr handles the
        # inline form (`o.items[0].v = 9`); this climbs the alias form to the param.
        if through_field:
            field_root = _storage_root(name)
            if field_root != name:
                self.mark_param_mutated(field_root, through_field=through_field)

    def mark_param_structurally_mutated(self, name: str) -> None:
        """Mark a parameter as structurally mutated (append/insert/clear/del/etc.).

        Structural mutations invalidate element references -- this is separate from
        mark_param_mutated which also fires for element-ref taking (a = items[0]).
        Traces loop variables back to their source iterables transitively.
        """
        if self.func.in_nested_def:
            self.func.nested_mutation_marks.append((name, False, True))
        if name == "self":
            self.func.current_self_struct_mutated = True
            return
        if name in self.func.current_param_names and name not in self.func.current_rebound_params:
            self.func.current_struct_mutated_param_names.add(name)
        for iterable in self.func.loop_var_iterable.get(name, ()):
            self.mark_param_structurally_mutated(_storage_root(iterable))

    def mark_param_returned(self, name: str,
                            _seen: 'set[str] | None' = None) -> None:
        """Mark a parameter as contributing to the return value (8b).

        Called when returning a reference derived from param storage, so we
        can record return_borrows_from on FunctionInfo. Mirrors mark_param_mutated
        but writes to current_returned_param_names instead.
        Traces loop variables back to their source iterables transitively.
        """
        # The field climb below re-enters a name the loan walk already left,
        # so the recursion needs its own guard.
        seen = set() if _seen is None else _seen
        if name in seen:
            return
        seen.add(name)
        if name == "self":
            self.func.current_returned_param_names.add("self")
            return
        if name in self.func.current_param_names and name not in self.func.current_rebound_params:
            self.func.current_returned_param_names.add(name)
        for iterable in self.func.loop_var_iterable.get(name, ()):
            self.mark_param_returned(_storage_root(iterable), seen)
        for src in self.func.bp_borrow_source_roots(name):
            self.mark_param_returned(src, seen)
        # A returned local alias (`t = b.m; return t`) lends what it borrows
        # from, and every source of a re-seated one: the caller cannot tell
        # which is live.
        for ultimate in self.func.borrow_tracker.all_storage_through_borrows(
                name, proven_only=True):
            self.mark_param_returned(ultimate, seen)
        # A field-path key (`self.inner`, from `return self.inner.get()` or
        # from an alias of `b.m`) lends its owning param. Unlike the mutation
        # mark there is nothing speculative to guard: a returned reference
        # into the field is a reference into the param -- unless the field
        # only POINTS at the storage.
        field_root = _storage_root(name)
        if field_root != name and not self._key_member_is_indirection(name):
            self.mark_param_returned(field_root, seen)

    def _key_member_is_indirection(self, key: str) -> bool:
        """`root.member` names a field that points AT storage (a `Ptr`, a
        borrowing view) rather than holding it, so a borrow reached through it
        is a borrow of the pointee and outlives `root`.

        Assumes the pointer aims OUTSIDE its own record; one aimed at a
        sibling field (`self.p = take_ptr(self.m)`) does lend `root`, and
        nothing here can tell."""
        root, _, member = key.partition(".")
        root_type = (self.func.current_scope.lookup(root)
                     if self.func.current_scope else None)
        if root_type is None:
            return False
        bare = unwrap_ref_type(unwrap_qualifiers(root_type))
        record = (self.registry.find_record(bare.name)
                  if isinstance(bare, NominalType) else None)
        if record is None:
            return False
        for info in self.registry.get_all_fields(record):
            if info.name == member:
                field_type = unwrap_ref_type(unwrap_qualifiers(info.type))
                return (field_type.is_pointer()
                        or is_borrowing_view_type(field_type))
        return False

    def mark_own_param_consumed(self, name: str) -> None:
        """Mark an Own[T] param as consumed (stored, forwarded, or returned)."""
        if name in self.func.current_param_names:
            self.func.current_consumed_own_params.add(name)

    # ------------------------------------------------------------------
    # View-type family generic accessors
    # ------------------------------------------------------------------

    def view_var_map(self, family: ViewTypeFamily) -> dict[str, int]:
        """Variable-name -> var_id mapping for the given family."""
        if family.pending_type_class is PendingStrType:
            return self.func.variable_to_str_var
        return self.func.variable_to_bytes_var

    def view_source_borrows_map(self, family: ViewTypeFamily) -> dict[str, set[int]]:
        """Source-storage -> set of borrowing var_ids for the given family."""
        if family.pending_type_class is PendingStrType:
            return self.func.str_source_borrows
        return self.func.bytes_source_borrows

    def view_pending_resolutions(self, family: ViewTypeFamily) -> list[int]:
        """Pending resolution list for the given family."""
        if family.pending_type_class is PendingStrType:
            return self.func.pending_str_resolutions
        return self.func.pending_bytes_resolutions

    def view_vars(self, family: ViewTypeFamily) -> dict[int, ViewVarInfo]:
        """Var-id -> ViewVarInfo registry for the given family."""
        if family.pending_type_class is PendingStrType:
            return self.str_vars
        return self.bytes_vars

    def view_needs_owned(self, info: ViewVarInfo) -> bool:
        """Whether the facts recorded so far force this view local to OWN.

        The one predicate behind every storage answer for a str/bytes local;
        `_resolve_pending_view_types` and `view_storage_verdict` both read it,
        so a use site and the resolution can never disagree on what the facts
        say.

        A generator/async body hoists every local into the resumable frame,
        which outlives the case-block temps and suspensions the sync
        view-safety judgment assumes the binding shares scope with -- so a
        non-static source forces owned storage there. Locals side of the param
        doctrine in `is_owned_in_coro_frame`; explicit StrView/BytesView
        annotations never enter Pending resolution, so the user's view
        contract is untouched.
        """
        func = self.func.current_function
        in_resumable = (isinstance(func, TpyFunction)
                        and (func.is_generator or func.is_async))
        return bool(
            info.initialized_from_owned
            or info.used_in_augassign
            or info.passed_to_promote_param
            or info.reassigned_from_owned
            or info.source_mutated
            or (in_resumable and info.frame_unsafe_source)
        )

    def view_storage_verdict(self, typ: 'TpyType | None') -> 'TpyType | None':
        """The storage the deduction has decided for an undecided str/bytes
        local -- the ONE answer every consumer of that storage asks for.

        `typ` is the local's binding type; anything that is not a pending view
        gets None, so a caller can spell `verdict(t) or t` and keep its own
        answer for a type that already states its storage. Once
        `_resolve_pending_view_types` has run the settled type is returned
        (`view_storage_settled` says so); before that it is the verdict the
        facts recorded so far imply, walking the alias chain because a source
        that owns forces its aliases to own. A later fact can only promote a
        view to owned, never the reverse, so a consumer that must act during
        body analysis reads a verdict that only gets stricter, and one that
        can wait defers until the answer is settled.
        """
        if not isinstance(typ, PendingViewType):
            return None
        family = typ.family
        registry = self.view_vars(family)
        root = registry.get(typ.var_id)
        if root is None:
            return None
        if root.resolved_type is not None:
            return root.resolved_type
        seen: set[int] = set()
        stack = [typ.var_id]
        while stack:
            var_id = stack.pop()
            if var_id in seen:
                continue
            seen.add(var_id)
            info = registry.get(var_id)
            if info is None:
                continue
            if (info.resolved_type == family.owned_type
                    or (info.resolved_type is None
                        and self.view_needs_owned(info))):
                return family.owned_type
            stack.extend(info.source_var_ids)
        return family.view_type

    def view_storage_settled(self, typ: 'TpyType | None') -> bool:
        """Whether `view_storage_verdict` for this local is final -- i.e. the
        resolution pass has run and no further binding can change it."""
        if not isinstance(typ, PendingViewType):
            return False
        info = self.view_vars(typ.family).get(typ.var_id)
        return info is not None and info.resolved_type is not None

    def next_view_var_id(self, family: ViewTypeFamily) -> int:
        """Allocate and return the next var_id for the given family."""
        if family.pending_type_class is PendingStrType:
            vid = self.str_var_counter
            self.str_var_counter += 1
            return vid
        vid = self.bytes_var_counter
        self.bytes_var_counter += 1
        return vid

    # ------------------------------------------------------------------
    # View-type mutation tracking
    # ------------------------------------------------------------------

    def mark_view_borrowers_mutated(self, storage: str, family: ViewTypeFamily) -> None:
        """Mark pending view-type borrowers of storage as source-mutated.

        Called at mutation sites so that view resolution falls back to
        the owned type when the view's source storage is mutated.
        Also invalidates field-path borrows: mutating ``p`` invalidates
        views borrowed from ``p.name``, ``p.field``, etc.
        """
        source_borrows = self.view_source_borrows_map(family)
        vars_registry = self.view_vars(family)
        keys = [storage]
        prefix = storage + "."
        keys.extend(k for k in source_borrows if k.startswith(prefix))
        for key in keys:
            var_ids = source_borrows.get(key)
            if not var_ids:
                continue
            for var_id in var_ids:
                info = vars_registry.get(var_id)
                if info is not None:
                    info.source_mutated = True

    def mark_all_view_borrowers_mutated(self, storage: str) -> None:
        """Mark borrowers across all view-type families as source-mutated.

        A key rooted at a borrowing local names the same storage as the key
        rooted at what it borrows from (``m = i`` makes ``m.name`` and
        ``i.name`` one buffer), and a view is registered under whichever
        spelling its own source used, so the write marks both.
        """
        keys = {storage, canonical_storage_key(self.func.borrow_tracker, storage)}
        for key in keys:
            for family in VIEW_TYPE_FAMILIES:
                self.mark_view_borrowers_mutated(key, family)

    # ------------------------------------------------------------------
    # Unified container literal lookup
    # ------------------------------------------------------------------

    def get_container_info(self, literal_id: int) -> ListLiteralInfo | DictLiteralInfo | SetLiteralInfo | None:
        """Look up container info across all container types by literal_id."""
        return (self.list_literals.get(literal_id)
                or self.dict_literals.get(literal_id)
                or self.set_literals.get(literal_id))

    def track_container_variable(
        self,
        var_name: str,
        pending_type: 'PendingListType | PendingDictType | PendingSetType',
        decl_line: int | None,
    ) -> None:
        """Register var-name -> literal_id mapping for any pending container type.

        Also sets variable_name and decl_line on the corresponding LiteralInfo.
        Single code path for list/dict/set -- adding a new container type means
        adding one branch here instead of duplicating blocks in statements.py.
        """
        literal_id = pending_type.literal_id
        info: ListLiteralInfo | DictLiteralInfo | SetLiteralInfo
        if isinstance(pending_type, PendingListType):
            self.func.variable_to_literal[var_name] = literal_id
            info = self.list_literals[literal_id]
        elif isinstance(pending_type, PendingDictType):
            self.func.variable_to_dict_literal[var_name] = literal_id
            info = self.dict_literals[literal_id]
        elif isinstance(pending_type, PendingSetType):
            self.func.variable_to_set_literal[var_name] = literal_id
            info = self.set_literals[literal_id]
        else:
            return
        info.variable_name = var_name
        info.decl_line = decl_line
