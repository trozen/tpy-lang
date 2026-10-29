"""
TurboPython Record Code Generation

Generates C++ structs from TurboPython records.
"""

from __future__ import annotations
import heapq
import io
from dataclasses import dataclass
from typing import Iterator, Sequence, TextIO, TYPE_CHECKING

from ..typesys import (
    TpyType, TypeRegistry, NominalType, OptionalType, OwnType, ReadonlyType,
    AutoReadonlyType, AutoOwnType, FinalType, ClassVarType,
    TypeParamRef, TypeParamKind, RecordInfo, TupleType, UnionType,
    types_held_by_value, unwrap_ref_type,
    unwrap_readonly, unwrap_optional_own, unwrap_send_sync,
    get_covariant_params, PtrType,
    bare_name, qualify_shadowed_nominals,
    emits_default_ctor, init_is_default_ctor, record_owner,
    type_value_init_indeterminate,
)
from ..parse import (
    TpyRecord, TpyEnum, TpyFunction, TpyStmt, TpyExprStmt, TpyAssign,
    TpyMethodCall, TpyFieldAccess, TpyName, TpyCoerce, TpyNestedDef,
    TpyPassStmt,
    TpyWith, TpyTry, TpyRaise,
    is_docstring, is_super_del_call, is_base_init_call,
    collect_name_refs, collect_top_level_local_names, expr_reads_self_field,
)
from ..identity_map import IdentityMap
from ..modules.type_resolution import is_native_iterable
from ..namespace import Namespace
from ..sema.registration import build_record_self_type, receiver_self_type

from .. import qnames
from . import emit_prims
from .context import (
    INDENT, CodeGenError, escape_cpp_name, enum_member_cpp, forward_param)
from ..parse import TpyNoneLiteral
from .functions import NULL_PROTOCOL_ARG_CPP, factory_default_to_cpp, type_member_default_cpp
from .protocols import (
    EmittedMember, MemberDef, MemberKind, MemberSignature, Shim, ShimKind,
    has_method_or_field, member_def, record_emitted_members,
)
from .int_literals import render_int_literal_value
from ..resumable_shape import ResumableShape
from ..sema.literal_utils import fixed_int_literal_value_from_expr
from ..type_def_registry import (
    is_span_iter,
    is_big_int_type, is_bytes_type, int_traits_of,
    has_view_param_form, view_to_owned_conv,
    enum_info_of, protocol_info_of, record_base_cpp, is_enum_type,
    is_set, is_dict,
)

if TYPE_CHECKING:
    from .context import CodeGenContext
    from .types import TypeResolver
    from .functions import FunctionGenerator, MethodEmitMode
    from .gen_async import AsyncCoroCodegen
    from .protocols import ProtocolGenerator


@dataclass
class _RecordDefPlan:
    """`RecordGenerator._member_def` applied to every member of one record
    (`record_emitted_members`, kept in ``members`` keyed on the def owning
    each signature, and its delegating ``shims``). An overload group's
    implementation(s) map to the group's partition; each variant has its
    own entry in ``variants``, keyed on its stub. A member forwarder
    (`operator[]`, `operator()`, `operator*`, `size()`) is placed from its
    target's entry (`_shim_mode`); a friend operator is always defined in
    the struct. Not in the plan, their bodies always in the struct: the
    synthesized members that read only the record itself (`__iter__`
    returning `*this`, `begin`/`end` over `__span__`, the exception
    overrides, a return exception's `__str__`, the covariant converting
    ctor)."""
    members: IdentityMap[TpyFunction, EmittedMember]
    shims: list[Shim]
    methods: IdentityMap[TpyFunction, MemberDef]
    variants: IdentityMap[TpyFunction, MemberDef]
    ctor: MemberDef | None
    # `G() : G(nullptr)`: the default ctor delegating to a ctor whose
    # protocol params are all optional.
    default_ctor: MemberDef | None
    copy_ops: MemberDef | None
    move_dtor: MemberDef | None


def _body_has_literal_throw(stmts: list[TpyStmt]) -> bool:
    """True if emitting `stmts` produces a literal C++ `throw` (vs a call).

    Only `raise` (throw expr) and the compiler-generated rethrow paths of
    `with` / `try` (finally + re-raise) emit a bare `throw`; implicit failures
    -- assert, bounds/deref checks, arithmetic -- lower to function calls that
    throw internally, so they do NOT trip `-Werror=terminate` in a noexcept
    destructor. A miss here fails loudly (the strict-flag test build rejects
    the unwrapped rethrow), never silently. `sub_bodies()` recurses through
    every compound statement (and returns [] for a nested def, whose body
    does not run at destruction time).
    """
    for s in stmts:
        if isinstance(s, (TpyRaise, TpyWith, TpyTry)):
            return True
        if any(_body_has_literal_throw(b) for b in s.sub_bodies()):
            return True
    return False


def _split_readonly_clone_pair(
    targets: Sequence[EmittedMember],
) -> tuple[EmittedMember, EmittedMember] | None:
    """Return (const_clone, mutable_clone) when `targets` (the members one
    shim forwards to) are exactly an @auto_readonly clone pair, otherwise
    None. Used by operator[] and operator*() emission to decide between
    dual-overload and single-overload paths.
    """
    if len(targets) != 2:
        return None
    const = next((t for t in targets if t.func.is_readonly), None)
    mutable = next((t for t in targets if not t.func.is_readonly), None)
    if const is None or mutable is None:
        return None
    return const, mutable


def nested_type_qnames(typ: TpyType, registry: TypeRegistry,
                       out: set[str]) -> None:
    """Add to `out` the qualified name of every class or enum nested in a
    class that `typ` names, at any depth: C++ can spell one only once its
    owner is complete."""
    if (isinstance(typ, NominalType) or is_enum_type(typ)) and "." in typ.name:
        q = typ.qualified_name()
        if q is None:
            # A parser placeholder (no qname) names a class of this module.
            info = registry.get_record_for_type(typ)
            q = info.qualified_name() if info is not None else None
        if q is not None:
            out.add(q)
    for inner in typ.inner_types():
        nested_type_qnames(inner, registry, out)


def _record_and_nested(record: TpyRecord) -> Iterator[TpyRecord]:
    yield record
    for nested in record.nested_records:
        yield from _record_and_nested(nested)


class RecordGenerator:
    """Generates C++ structs from TurboPython records."""

    def __init__(
        self,
        ctx: CodeGenContext,
        types: TypeResolver,
        protocols: ProtocolGenerator,
        functions: FunctionGenerator,
    ):
        self.ctx = ctx
        self.types = types
        self.protocols = protocols
        self.functions = functions
        self.gen_async: AsyncCoroCodegen  # Set by CodeGenerator after init
        self._def_plans: IdentityMap[TpyRecord, _RecordDefPlan] = IdentityMap()

    def sort_records_by_storage(
        self, records: list[TpyRecord], *,
        defined_early: frozenset[str] | set[str] = frozenset(),
        nested: bool = False,
    ) -> list[TpyRecord]:
        """Order `records` so each follows every record its layout stores
        inline (a field or base held by value) and the owner of every nested
        class its declarations name -- C++ needs those complete at the
        struct.

        A stable re-sort of the baseline (`_inheritance_order` for top-level
        records; source order for the nested siblings of one owner): the
        earliest remaining record whose needs are all emitted goes next, so an order that already satisfies every need is kept as
        is. With none ready (a by-value cycle, which has no C++ layout), the
        earliest remaining record goes next. `defined_early` records are
        emitted before this order is used, so they are never waited on.
        Native records are excluded (no C++ struct generation needed).
        """
        if not nested:
            records = self._inheritance_order(
                [r for r in records if not self._is_native(r)])
        needs = self._record_order_needs(records, defined_early)
        position = {r.name: i for i, r in enumerate(records)}
        waiting = {r.name: len(needs[r.name]) for r in records}
        dependents: dict[str, list[str]] = {r.name: [] for r in records}
        for name, deps in needs.items():
            for dep in deps:
                dependents[dep].append(name)
        # Kahn over baseline positions: the heap holds the ready records.
        ready = [i for i, r in enumerate(records) if not waiting[r.name]]
        heapq.heapify(ready)
        emitted = [False] * len(records)
        earliest_left = 0
        result: list[TpyRecord] = []
        while len(result) < len(records):
            if ready:
                i = heapq.heappop(ready)
            else:
                while emitted[earliest_left]:
                    earliest_left += 1
                i = earliest_left
            emitted[i] = True
            result.append(records[i])
            for dependent in dependents[records[i].name]:
                waiting[dependent] -= 1
                if waiting[dependent] == 0 and not emitted[position[dependent]]:
                    heapq.heappush(ready, position[dependent])
        return result

    def _inheritance_order(self, records: list[TpyRecord]) -> list[TpyRecord]:
        """Topological order with every base class before its derived ones
        and every set / dict key record before its holder (Kahn); a leftover
        cycle is appended in source order."""
        record_by_name = {r.name: r for r in records}
        dependencies: dict[str, set[str]] = {r.name: set() for r in records}
        registry = self.ctx.analyzer.registry
        # A parent is a dependency when it IS one of these records -- a
        # foreign base sharing a local record's name is not.
        own_name_of: IdentityMap[RecordInfo, str] = IdentityMap(
            (ri, r.name) for r in records
            if (ri := registry.get_record(r.name)) is not None)
        for record in records:
            record_info = registry.get_record(record.name)
            if record_info is None:
                continue
            for p in record_info.parents:
                if not (isinstance(p, NominalType) and p.is_user_record):
                    continue
                parent_info = registry.get_record_for_type(p)
                parent_name = (own_name_of.get(parent_info)
                               if parent_info is not None else None)
                if parent_name is not None:
                    dependencies[record.name].add(parent_name)
            # The hash split needs no key-before-holder order, but bodies
            # kept in the struct can rely on it: a deduced-return member
            # and a friend operator returning the key by value
            # (BUGS.md#deduced-return-member-body-uses-later-declaration).
            for fld in record_info.fields:
                if fld.type is None:
                    continue
                hash_deps: set[str] = set()
                self._collect_hash_key_records(fld.type, hash_deps)
                for dep in hash_deps:
                    if dep in record_by_name and dep != record.name:
                        dependencies[record.name].add(dep)

        result = []
        result_names: set[str] = set()
        no_deps = [name for name, deps in dependencies.items() if not deps]

        while no_deps:
            name = no_deps.pop(0)
            result.append(record_by_name[name])
            result_names.add(name)

            # Remove this record from all dependents
            for dep_name, deps in dependencies.items():
                if name in deps:
                    deps.remove(name)
                    if not deps and dep_name not in result_names:
                        no_deps.append(dep_name)

        for record in records:
            if record.name not in result_names:
                result.append(record)

        return result

    def _collect_hash_key_records(self, typ: TpyType, deps: set[str]) -> None:
        """Walk a type peeling wrappers; for every `set[K]` / `dict[K, V]`
        found, collect K's user-record name into `deps`."""
        if isinstance(typ, (OwnType, ReadonlyType, AutoReadonlyType,
                            AutoOwnType, FinalType, ClassVarType)):
            self._collect_hash_key_records(typ.wrapped, deps)
            return
        if isinstance(typ, PtrType):
            self._collect_hash_key_records(typ.pointee, deps)
            return
        if isinstance(typ, OptionalType):
            self._collect_hash_key_records(typ.inner, deps)
            return
        if isinstance(typ, UnionType):
            for member in typ.members:
                self._collect_hash_key_records(member, deps)
            return
        if isinstance(typ, TupleType):
            for member in typ.element_types:
                self._collect_hash_key_records(member, deps)
            return
        if isinstance(typ, NominalType):
            if (is_set(typ) or is_dict(typ)) and typ.type_args:
                key = typ.type_args[0]
                if isinstance(key, TpyType):
                    key_name = self._user_record_name(key)
                    if key_name is not None:
                        deps.add(key_name)
                    self._collect_hash_key_records(key, deps)
                for val in typ.type_args[1:]:
                    if isinstance(val, TpyType):
                        self._collect_hash_key_records(val, deps)
                return
            for arg in typ.type_args:
                if isinstance(arg, TpyType):
                    self._collect_hash_key_records(arg, deps)

    def _user_record_name(self, typ: TpyType) -> str | None:
        """Peel wrappers from `typ` and return the underlying user-record
        name if any, else None. Used by hash-element dependency walking.
        """
        if isinstance(typ, (OwnType, ReadonlyType, AutoReadonlyType,
                            AutoOwnType, FinalType, ClassVarType)):
            return self._user_record_name(typ.wrapped)
        if isinstance(typ, PtrType):
            return self._user_record_name(typ.pointee)
        if isinstance(typ, OptionalType):
            return self._user_record_name(typ.inner)
        if isinstance(typ, NominalType) and typ.is_user_record:
            return typ.name
        return None

    def _record_order_needs(
        self, records: list[TpyRecord], defined_early: frozenset[str] | set[str],
    ) -> dict[str, set[str]]:
        """For each of `records`, the others it must follow. A record emits
        its nested records inside itself, so their needs are its own, and a
        need of a class nested in one of `records` is a need of that one."""
        registry = self.ctx.analyzer.registry
        owner_of: IdentityMap[RecordInfo, str] = IdentityMap()
        owner_of_qname: dict[str, str] = {}
        for record in records:
            for member in _record_and_nested(record):
                info = registry.get_record(member.name)
                if info is None:
                    continue
                owner_of[info] = record.name
                owner_of_qname[info.qualified_name()] = record.name

        needs: dict[str, set[str]] = {r.name: set() for r in records}
        for record in records:
            if record.name in defined_early:
                continue
            targets: list[str | None] = []
            for member in _record_and_nested(record):
                info = registry.get_record(member.name)
                # A generic class's members are instantiated with its uses:
                # what `G[B]` stores is a need of the record holding it.
                if member.type_params or info is None or info.is_native:
                    continue
                slots = list(info.parents) + [
                    unwrap_ref_type(f.type) for f in info.fields if f.type is not None]
                for slot in slots:
                    for held in types_held_by_value(slot, stop_at_indirecting=True):
                        if (isinstance(held, NominalType)
                                and (held_info := registry.get_record_for_type(held))
                                is not None):
                            targets.append(owner_of.get(held_info))
                named: set[str] = set()
                for typ in slots + self._declared_signature_types(member):
                    nested_type_qnames(typ, registry, named)
                targets.extend(owner_of_qname.get(q) for q in named)
            needs[record.name] = {
                t for t in targets
                if t is not None and t != record.name and t not in defined_early}
        return needs

    def _declared_signature_types(self, record: TpyRecord) -> list[TpyType]:
        """The types the in-struct declarations of `record`'s emitted members
        spell: parameters, template bounds, and returns other than a deduced
        `auto`. A `@cpp_template` member declares nothing."""
        out: list[TpyType] = []
        plan = self._def_plan(record)
        if plan is None:
            return out
        for entry in plan.members.values():
            if entry.kind is MemberKind.EXPANDED:
                continue
            method = entry.func
            out.extend(t for _, t in method.params if t is not None)
            out.extend(method.type_param_bounds.values())
            if method.return_type is not None and not entry.sig.deduced:
                out.append(method.return_type)
        return out

    def _is_field_type_nocopy(self, typ: TpyType) -> bool:
        """Check if a field type is nocopy (for comment generation)."""
        if isinstance(typ, (ReadonlyType, OwnType)):
            return self._is_field_type_nocopy(typ.wrapped)
        if isinstance(typ, OptionalType):
            return self._is_field_type_nocopy(typ.inner)
        record = self.ctx.analyzer.registry.get_record_for_type(typ)
        if record is not None and record.is_nocopy:
            return True
        if isinstance(typ, NominalType) and typ.type_args:
            if record is not None and record.has_copy:
                return False
            return any(
                isinstance(a, TpyType) and self._is_field_type_nocopy(a)
                for a in typ.type_args
            )
        return False

    def _find_nocopy_field(self, record_info: RecordInfo) -> str | None:
        """Find the name of the first nocopy field, for diagnostic comments."""
        for f in record_info.fields:
            if self._is_field_type_nocopy(f.type):
                return f.name
        for p in record_info.parents:
            parent_rec = self.ctx.analyzer.registry.get_record_for_type(p)
            if parent_rec is not None and parent_rec.is_nocopy:
                return None  # parent nocopy, not a field
        return None

    def _needs_throwable_auto_emit(self, record_info: 'RecordInfo') -> bool:
        """True iff codegen should auto-emit clone()/__raise__()/what() on this
        record. Reads the `implements_throwable` fact materialized at sema
        registration time. @native records and `builtins.BaseException` are
        skipped -- their overrides come from `TPY_THROWABLE_VIRTUALS` in the
        runtime header (Stage 4c retires the macro and folds BaseException
        into the auto-emit path).
        """
        if record_info.is_native:
            return False
        if record_info.qualified_name() == qnames.BASE_EXCEPTION:
            return False
        return record_info.implements_throwable

    def _is_native(self, record_or_name) -> bool:
        """Check if a record/type is a native import (no C++ generation needed)."""
        name = record_or_name.name if hasattr(record_or_name, 'name') else record_or_name
        record_info = self.ctx.analyzer.registry.get_record(name)
        return record_info is not None and record_info.is_native

    def _record_shadows_local_type(self, record: TpyRecord) -> bool:
        """True if a member shadows a same-named local type in C++ record scope,
        so type references inside must render fully-qualified. Reads the fact
        sema computed once (`RecordInfo.shadows_local_type`) over the full C++
        member set (own + MRO-inherited methods/fields/properties/class-constants)
        -- codegen does not re-derive it."""
        ri = self.ctx.analyzer.registry.get_record(record.name)
        return ri is not None and ri.shadows_local_type

    def gen_record_decl(self, out: TextIO, record: TpyRecord) -> None:
        """Generate a struct declaration for a record."""
        if not self._is_native(record) and self._record_shadows_local_type(record):
            with qualify_shadowed_nominals():
                self._gen_record_decl(out, record)
        else:
            self._gen_record_decl(out, record)

    def _gen_record_decl(self, out: TextIO, record: TpyRecord) -> None:
        """Generate a struct declaration for a record."""
        # Native records don't generate C++ structs -- they're defined in external headers
        if self._is_native(record):
            return

        # Get record info for inheritance information
        record_info = self.ctx.analyzer.registry.get_record(record.name)

        # Emit class header as source comments (preceding comments + class line)
        if record.loc:
            self.ctx.emit_declaration_echo(out, record.loc)

        # Generate template prefix for generic records
        if record.type_params:
            template_header = self.protocols.gen_record_template_header(
                record.type_params, record.type_param_bounds, record.type_param_kinds
            )
            out.write(f"{template_header}\n")

        # Generate struct with optional inheritance
        # Collect base classes: user-defined parents (in source / C3 order) + @dynamic protocol bases
        bases = []
        if record_info:
            for p in record_info.parents:
                bases.append(record_base_cpp(record_info, p))
            for proto in record_info.implemented_protocols:
                proto_info = protocol_info_of(proto)
                if proto_info and proto_info.is_dynamic:
                    bases.append(self.protocols.get_dynamic_base_name(proto))
        # Use short name for nested types (e.g., "Inner" not "Outer.Inner")
        short_name = bare_name(record.name)
        cpp_rec_name = escape_cpp_name(short_name)
        if bases:
            out.write(f"struct {cpp_rec_name} : {', '.join(bases)} {{\n")
        else:
            out.write(f"struct {cpp_rec_name} {{\n")

        # Nested enums (before fields so they can be used as field types)
        for nested_enum in record.nested_enums:
            self._gen_nested_enum_decl(out, nested_enum)

        # Nested records (before fields so they can be used as field types)
        for nested_rec in self.sort_records_by_storage(record.nested_records, nested=True):
            buf = io.StringIO()
            self.gen_record_decl(buf, nested_rec)
            for line in buf.getvalue().splitlines(True):
                out.write(INDENT + line if line.strip() else line)
            out.write("\n")

        # Fields
        for fld in record.fields:
            self.ctx.emit_declaration_echo(out, fld.loc, INDENT)
            cpp_type = self.types.type_to_cpp(fld.type)
            default = ""
            if fld.default_value is not None:
                # An int-literal default is pre-rendered bare by the parser
                # (no type context); re-render it here through the shared
                # chokepoint with the resolved field type so a >int32 value
                # pins its C++ width (a bare `long` converts to BigInt
                # ambiguously on macOS). Mirrors the enum-member default arm
                # below, which likewise renders from default_expr at codegen.
                # fixed_int_literal_value_from_expr peels a fixed-int ctor
                # wrapper (`int64(x)`) too, matching default_to_cpp so the
                # field member-init and the generated ctor param agree.
                iv = (fixed_int_literal_value_from_expr(fld.default_expr)
                      if fld.default_expr is not None else None)
                if iv is not None:
                    val = render_int_literal_value(
                        iv, fld.type,
                        default_int_type=self.ctx.analyzer.ctx.default_int_type,
                        type_to_cpp=lambda t: t.to_cpp())
                else:
                    val = fld.default_value
                # Parser renders None -> "std::nullopt" without type context;
                # raw-pointer fields need "nullptr" instead. Can't reuse
                # `default_to_cpp` here -- it returns borrow-form defaults
                # which would also (wrongly) flip Optional-of-record fields
                # from std::nullopt to nullptr.
                if val == "std::nullopt" and isinstance(fld.type, PtrType):
                    val = "nullptr"
                default = f" = {val}"
            elif fld.default_expr is not None and not fld.is_factory_default:
                # Non-literal constant defaults (the enum-member shape that
                # registration validated, e.g. `c: Color = Color.RED`).
                # Rendered directly with the canonical enum qualifier --
                # the default expr is never sema-analyzed, so the generic
                # expression render (which needs resolved facts) can't be used.
                init = self._render_enum_member_default(fld.default_expr)
                if init is not None:
                    # A `str` / `bytes` class constant is its view form
                    # (`static constexpr std::string_view`); the owned field
                    # constructs from it explicitly.
                    if has_view_param_form(fld.type):
                        init = f"{view_to_owned_conv(fld.type)}({init})"
                    default = f" = {init}"
            elif fld.is_factory_default:
                default = f" = {factory_default_to_cpp(fld.type)}"
            out.write(f"{INDENT}{cpp_type} {escape_cpp_name(fld.name)}{default};\n")
        if record.enum_companion_of is not None:
            # An enum's companion wraps the member it is called on: a call
            # renders `__enum_E{recv}.m(args)`, so the receiver is a postfix
            # expression sequenced before the arguments, as a record's is.
            enum_t = receiver_self_type(record, self.ctx.analyzer.registry)
            out.write(f"{INDENT}{self.types.type_to_cpp(enum_t)} self;\n")

        # @native records skip emission -- the user's header owns the storage.
        # Use the expression generator so tuples / type-coerced literals emit
        # the same way module-level Final constants do.
        if record_info and not record_info.is_native:
            # Earlier constants of this body are in scope for the later ones
            # (`DOUBLE: Final[int32] = BASE * 2`), on top of the module's
            # Final globals -- accumulated in declaration order so a class
            # constant shadowing a global wins, as it does in C++.
            const_scope = self._thir_const_scope(record_info)
            for cc_name, cc_fld in record_info.class_constants.items():
                self.ctx.emit_declaration_echo(out, cc_fld.loc, INDENT)
                cpp_type = self.types.type_to_cpp(cc_fld.type)
                if cc_fld.default_expr is not None:
                    init = self._thir_class_const_init(cc_name, cc_fld,
                                                       const_scope)
                    self._thir_add_const(const_scope, cc_name, cc_fld.type)
                else:
                    init = cc_fld.default_value if cc_fld.default_value is not None else "{}"
                if not record_info.is_final_class_constant(cc_name):
                    storage = "static inline"
                elif cc_name in record_info.runtime_class_constants:
                    storage = "static inline const"
                else:
                    storage = "static constexpr"
                out.write(f"{INDENT}{storage} {cpp_type} {escape_cpp_name(cc_name)} = {init};\n")

        # Drop flag for classes with __del__ -- prevents double-drop after move.
        # Only emit on the root class; children inherit the parent's flag so that
        # all destructors in the chain check the same field.
        if record.del_method is not None and not self.ctx.any_ancestor_has_del(record.name):
            out.write(f"{INDENT}bool __tpy_owned_ = true;\n")

        out.write("\n")

        plan = self._def_plan(record)
        assert plan is not None
        # Constructor: declared here, defined at namespace scope (the plan's
        # partition) -- an in-struct MIL/body that references a
        # mutually-recursive sibling record would need its complete type
        # before any struct order can provide it.
        if record.init_method:
            has_params = bool(record.init_method.params)
            self.ctx.emit_declaration_echo(out, record.init_method.loc, INDENT)
            if has_params:
                # Default constructor: delegate to the template ctor with
                # nullptr so the `__init__` body executes (else branch runs
                # via if constexpr); an `__init__` that is not the default
                # ctor leaves `= default` to the record's fact.
                if plan.default_ctor is not None:
                    out.write(f"{INDENT}{cpp_rec_name}();\n")
                elif (record_info is not None
                      and not init_is_default_ctor(record_info)
                      and emits_default_ctor(record_info)):
                    out.write(f"{INDENT}{cpp_rec_name}() = default;\n")
                template_header, cpp_params = self._ctor_cpp_params(
                    record, emit_defaults=True)
                if template_header:
                    out.write(f"{INDENT}{template_header}")
                out.write(f"{INDENT}explicit {cpp_rec_name}({cpp_params});\n")
            else:
                out.write(f"{INDENT}{cpp_rec_name}();\n")
        else:
            # No __init__: plain records stay C++ aggregates (no ctor declared).
            # Records with __del__/@nocopy/__copy__ get user-declared copy/move
            # ops, which suppress the implicit default ctor -- restore it with
            # `= default;` where the record has one (`emits_default_ctor`).
            if record_info and record_info.inherits_init_from is not None:
                # `using Foo::Foo` only compiles if both halves match the C++
                # class name. @native renames let the Python and C++ short
                # names diverge, so derive the ctor half from the C++ side,
                # not parent.name. The injected-class-name is the bare short
                # name (no namespace, no template args), so take the last ::
                # segment of the args-free base name -- never parse to_cpp()
                # output, whose template args can themselves contain `::`.
                parent_cpp = record_info.inherits_init_from.to_cpp()
                base_name = record_info.inherits_init_from.to_cpp_base_name()
                parent_cpp_short = base_name.rsplit("::", 1)[-1]
                out.write(f"{INDENT}using {parent_cpp}::{parent_cpp_short};\n")
                registry = self.ctx.analyzer.registry
                if (registry.inherits_template_init(record_info)
                        and registry.get_record_for_type(
                            record_info.inherits_init_from)
                        is registry.init_owner(record_info)):
                    # A builtin base's `__init__` builds the base value, and
                    # C++ does not inherit the base's copy / move ctors, so a
                    # subclass's `super().__init__(...)` reaches the base
                    # through this one.
                    out.write(
                        f"{INDENT}explicit {cpp_rec_name}({parent_cpp} __base)"
                        f" : {parent_cpp}(std::move(__base)) {{}}\n")
            if record_info and (record_info.is_nocopy or record_info.has_del
                                or record_info.has_copy):
                if emits_default_ctor(record_info):
                    out.write(f"{INDENT}{cpp_rec_name}() = default;\n")

        # Copy/move ops for @nocopy, __del__, or __copy__ classes: declared
        # here, defined at namespace scope like the constructor.
        if record_info and record_info.has_copy:
            self._gen_copy_ops(out, record, mode="decl")
        elif record_info and (record_info.is_nocopy or record_info.has_del):
            if record_info.is_nocopy:
                out.write(f"{INDENT}// non-copyable")
                nocopy_field = self._find_nocopy_field(record_info)
                if record.is_nocopy:
                    out.write(" (@nocopy)")
                elif nocopy_field:
                    out.write(f" (field '{nocopy_field}')")
                out.write("\n")
            out.write(f"{INDENT}{cpp_rec_name}(const {cpp_rec_name}&) = delete;\n")
            out.write(f"{INDENT}{cpp_rec_name}& operator=(const {cpp_rec_name}&) = delete;\n")
            if record_info.is_nocopy and not record_info.has_del:
                out.write(f"{INDENT}{cpp_rec_name}({cpp_rec_name}&&) = default;\n")
                out.write(f"{INDENT}{cpp_rec_name}& operator=({cpp_rec_name}&&) = default;\n")

        # Generate destructor if __del__ is defined
        self._gen_move_and_destructor(out, record, mode="decl")

        # Generate converting move constructor for covariant generics
        self._gen_covariant_converting_ctor(out, record)

        # Synthesize __iter__() -> self for iterator types (has __next__ but no explicit __iter__)
        has_next = any(m.name == "__next__" for m in record.methods)
        has_iter = any(m.name == "__iter__" for m in record.methods)
        if has_next and not has_iter:
            out.write(f"\n{INDENT}auto& __iter__() {{ return *this; }}\n")
        if has_iter and self._overrides_inherited_range(record):
            # The base's begin()/end() stays visible in C++; every consumer
            # must run this class's `__iter__`, as a loop over it does.
            out.write(f"{INDENT}using tpy_iter_dunder = void;\n")

        # Synthesize begin()/end() from __span__() so span-backed user types
        # satisfy std::ranges::input_range. The returned span's iterators
        # point into the underlying storage (owned by self), not into the
        # prvalue span, so they remain valid for the lifetime of self even
        # though the span itself is a temporary.
        #
        # Skipped when the user has a __iter__ whose semantics differ from
        # __span__ -- otherwise comprehensions/genexprs (which call
        # begin/end) would silently diverge from for-loops (which use
        # __iter__/__next__). __iter__ returning SpanIter[T] is the
        # equivalent case: SpanIter iterates the same underlying span,
        # so begin/end-via-__span__ matches __iter__/__next__ semantically.
        #
        # Must appear before user methods so concept constraints see a
        # consistent declaration state when evaluated inside method bodies.
        has_begin = has_method_or_field(record, "begin")
        has_end = has_method_or_field(record, "end")
        if not has_begin and not has_end:
            record_info = self.ctx.analyzer.registry.get_record(record.name)
            if record_info and "__span__" in record_info.methods:
                iter_diverges = False
                if has_iter:
                    iter_overloads = record_info.methods.get("__iter__")
                    iter_diverges = not (
                        iter_overloads
                        and is_span_iter(iter_overloads[0].return_type)
                    )
                if not iter_diverges:
                    out.write(f"\n{INDENT}auto begin() {{ return this->__span__().begin(); }}\n")
                    out.write(f"{INDENT}auto end() {{ return this->__span__().end(); }}\n")
                    # Only emit const overloads when a readonly __span__ exists;
                    # otherwise `this->__span__()` on `const this` fails to compile.
                    span_overloads = record_info.methods["__span__"]
                    if any(m.is_readonly for m in span_overloads):
                        out.write(f"{INDENT}auto begin() const {{ return this->__span__().begin(); }}\n")
                        out.write(f"{INDENT}auto end() const {{ return this->__span__().end(); }}\n")

        self._gen_record_methods(out, record, None)
        self._gen_shims(out, record, None)

        # Tag consumed by:
        #   * the default-repr runtime template in dunder.hpp (only fires
        #     when T has no __repr__; SFINAE-guards on its own);
        #   * the tpy::type_name<T>() registry in type_name.hpp (used by
        #     Any cast / unhashable panic messages).
        # Emit unconditionally so panics on records that *do* define
        # __repr__ still report the TPy-friendly module-qualified name
        # (`__main__.Animal`, `mypkg.mymod.Outer.Inner`) rather than the
        # demangled C++ form. Module is taken from the analyzer's
        # `module_name` (which is `"__main__"` for the entry point,
        # matching `__name__`) rather than codegen's which uses the file
        # basename.
        record_info = self.ctx.analyzer.registry.get_record(record.name)

        # Phase 20: auto-emit Throwable ABI overrides on every user-defined
        # Throwable subclass. Stage 4's spec move auto-emits these
        # unconditionally on every Throwable subclass; Stage 2 forward-ports
        # the auto-emit for user-defined classes so the headline invariant
        # (BUGS.md exception-slicing) holds for user code too.
        # `what()` is emitted only on the BaseException-rooted leaves (i.e.
        # not on BaseException itself, which still lives in core.hpp pre-
        # Stage-4c); the override reads the `message` field that BaseException
        # provides. Stage 4c moves the whole hierarchy to TPy and the
        # auto-emit path becomes the only definer of these methods.
        if record_info and self._needs_throwable_auto_emit(record_info):
            out.write(
                f"\n{INDENT}[[nodiscard]] std::unique_ptr<::tpy::Throwable> "
                f"clone() const override {{ "
                f"return std::make_unique<{cpp_rec_name}>(*this); }}\n"
            )
            out.write(
                f"{INDENT}[[noreturn]] void __raise__() const override "
                f"{{ throw *this; }}\n"
            )
            out.write(
                f"{INDENT}const char* what() const noexcept override "
                f"{{ return this->message.c_str(); }}\n"
            )

        # A return exception has no thrown base to supply `__str__` over its
        # message, so a class that declares the field gets the accessor here;
        # without the field the empty value base answers "".
        if (record_info and record_info.is_return_exception
                and not record_info.is_native
                and not record_info.get_method_overloads("__str__")
                and any(f.name == qnames.EXCEPTION_MESSAGE_FIELD
                        for f in record.fields)):
            out.write(
                f"\n{INDENT}std::string_view __str__() const "
                f"{{ return {qnames.EXCEPTION_MESSAGE_FIELD}; }}\n"
            )

        has_str_repr = self._record_has_str_repr(record_info)
        native_repr = self._inherited_native_repr(record_info)
        if native_repr is not None:
            base_cpp = self.types.type_to_cpp(native_repr)
            # Streamed, so a printed container element writes it in place.
            out.write(
                f"\n{INDENT}void __repr_into__(std::ostream& os) const {{\n"
                f"{INDENT}{INDENT}::tpy::print_base_of<{base_cpp}>(os, "
                f'"{bare_name(record.name)}", *this);\n'
                f"{INDENT}}}\n"
                f"\n{INDENT}std::string __repr__() const {{\n"
                f"{INDENT}{INDENT}std::ostringstream os;\n"
                f"{INDENT}{INDENT}__repr_into__(os);\n"
                f"{INDENT}{INDENT}return os.str();\n"
                f"{INDENT}}}\n")
        elif self._shadows_inherited_repr_into(record_info):
            # print_element prefers an inherited streaming repr; the
            # record's own `__repr__` is the one that runs.
            out.write(
                f"\n{INDENT}void __repr_into__(std::ostream& os) const {{ "
                f"os << __repr__(); }}\n")
        python_module = self.ctx.analyzer.ctx.module_name
        qualified_name = f"{python_module}.{record.name}"
        out.write(
            f'{INDENT}static constexpr std::string_view '
            f'__tpy_class_name__ = "{qualified_name}";\n'
        )

        out.write("};\n")
        # operator<< and nested ostream operators must be at namespace scope,
        # so skip for nested records (they are emitted by the top-level parent)
        if "." not in record.name:
            self._gen_record_ostream(out, record, has_str_repr=has_str_repr)
            self._gen_nested_ostream_operators(out, record)

    def _thir_const_scope(self, record_info) -> 'dict | None':
        """The constant names a class body's initializers may reference, or
        None when the class declares none. Mutated in declaration order by
        the caller."""
        if not record_info.class_constants:
            return None
        from ..thir.constants import final_global_scope
        return final_global_scope(self.ctx.analyzer, self.ctx.final_globals)

    def _thir_add_const(self, const_scope: 'dict | None', name: str,
                        declared) -> None:
        """Bring one already-emitted class constant into scope for the
        constants declared after it."""
        if const_scope is None:
            return
        from ..thir.constants import add_constant_scope_entry
        add_constant_scope_entry(const_scope, name, declared, self.ctx.analyzer)

    def _thir_class_const_init(self, cc_name: str, cc_fld,
                               const_scope: 'dict') -> str:
        """Render a class constant's initializer through THIR."""
        # `thir.constants` imports `thir.emit`, so an eager import here would
        # be a codegen_cpp <-> thir cycle; `thir.reject` only rides along.
        from ..thir.constants import lower_constant
        from ..thir.reject import begin_attempt, commit_attempt, reject_attempt
        begin_attempt()
        init = lower_constant(
            cc_fld.default_expr, cc_fld.type, self.ctx.analyzer,
            const_scope=const_scope,
            render_type=self.types.type_to_cpp,
            render_type_stored=self.types.type_to_cpp_stored,
            render_resolve=self.types.resolve_type)
        if init is None:
            reject_attempt("class_const",
                           where=f"in the initializer of '{cc_name}'",
                           loc=cc_fld.loc)
        commit_attempt()
        return init

    def _gen_nested_ostream_operators(self, out: TextIO, record: TpyRecord) -> None:
        """Emit operator<< for all nested records (must be at namespace scope)."""
        for nested_rec in record.nested_records:
            if self._is_native(nested_rec):
                continue
            self._gen_record_ostream(out, nested_rec)
            self._gen_nested_ostream_operators(out, nested_rec)

    def _ctor_all_protocols_optional(self, record: TpyRecord) -> bool:
        """True when the __init__ ctor's default constructor delegates to the
        template ctor with nullptr: the `__init__` is the default ctor (the
        record's `init_is_default_ctor` fact) but takes protocol params, and
        a template ctor never serves as one. Shared by the decl and def
        passes."""
        init = record.init_method
        if init is None or not init.params:
            return False
        record_info = self.ctx.analyzer.registry.get_record(record.name)
        return bool(record_info is not None
                    and init_is_default_ctor(record_info)
                    and self.functions.protocols.get_all_protocol_params(init.params))

    def _ctor_cpp_params(self, record: TpyRecord,
                         *, emit_defaults: bool) -> tuple[str, str]:
        """Render the __init__ ctor's C++ param list; returns
        ``(template_header, params)`` where the header is "" unless the ctor
        has structural-protocol params. C++ rejects default arguments repeated
        on both the declaration and an out-of-line definition, so the def pass
        passes ``emit_defaults=False``."""
        init = record.init_method
        init_defaults = init.defaults if init.defaults else None
        proto_params = self.functions.protocols.get_all_protocol_params(init.params)
        has_dynamic = self.functions._has_dynamic_protocol_params(init.params)
        # Ctor params that the body mutates (typically via value->Ptr
        # coercion at a callee site or assignment to a Ptr storage)
        # must drop the perf-default `const T&` -- the address-take
        # produces `T*`, not `const T*`. Regular methods plumb this
        # through gen_params; constructors used to skip it because
        # ctors must accept temporaries, but a mutated param can't
        # accept a temporary anyway (sema's requires_mutable_lvalue
        # rejects the rvalue call before reaching codegen).
        init_mp = self.functions._get_method_mutated_params(init, record.name)
        if proto_params or has_dynamic:
            cpp_params = self.functions.gen_params_with_protocols(
                init.params,
                init.type_params,
                const_params=True,
                mutated_params=init_mp,
                defaults=init_defaults,
                emit_defaults=emit_defaults,
                is_member=True,
            )
            template_header = ""
            if proto_params:
                template_header = self.functions.protocols.gen_combined_template_header(
                    init.type_params or [], proto_params,
                    record.type_param_bounds or None,
                    emit_defaults=emit_defaults,
                )
            return template_header, cpp_params
        init_ae = self.functions._get_method_addr_escapes(init, record.name)
        cpp_params = self.functions.gen_params(
            init.params,
            init.type_params,
            const_params=True,
            mutated_params=init_mp,
            addr_escapes_params=init_ae,
            defaults=init_defaults,
            emit_defaults=emit_defaults,
            class_type_params=set(record.type_params) if record.type_params else None,
            is_member=True,
        )
        return "", cpp_params

    def _emit_ctor_tail(self, out: TextIO, record: TpyRecord,
                        *, body_indent_level: int) -> None:
        """Emit the ctor's ` : mil... { body }` tail after the signature.
        ``body_indent_level`` is 2 in-struct and 1 at namespace scope.

        The member-init list and body tail both come off the lowered
        THIRConstructor, which already carries the hoisted inits; the
        signature is emitted by the caller."""
        thir_ctor = self.ctx.thir_constructors.get(record.init_method)
        if thir_ctor is None:
            raise CodeGenError(
                f"internal error: no lowered constructor for '{record.name}'",
                record.init_method.loc)
        from ..thir.emit import (
            ModuleCounter, TempSink,
            emit_thir_constructor_tail,
        )
        with self.ctx.temps.function_scope():
            emit_thir_constructor_tail(
                out, thir_ctor,
                temps=TempSink(self.ctx),
                with_counter=ModuleCounter(self.ctx, "with_counter"),
                try_counter=ModuleCounter(self.ctx, "try_except_counter"),
                finally_guard_counter=ModuleCounter(
                    self.ctx, "finally_guard_counter"),
                body_indent_level=body_indent_level)

    def _def_prefix(self, record: TpyRecord,
                    mode: MethodEmitMode) -> tuple[str, str]:
        """What opens an out-of-class member definition of `record`: the
        class template's head ("" for a plain class), and `inline ` in the
        header partition. Apart, so a member's own template head can go
        between them."""
        return (self.protocols.record_template_head(record.name),
                "inline " if mode == "def_hpp" else "")

    def _gen_ctor_def(self, out: TextIO, record: TpyRecord,
                      *, want: MemberDef) -> None:
        """Emit the out-of-line ctor definition(s) for ``record`` whose plan
        entry is ``want`` -- the def-side counterpart of the in-struct
        declarations that `_gen_record_decl` emits."""
        init = record.init_method
        plan = self._def_plan(record)
        if init is None or plan is None:
            return
        head, inline = self._def_prefix(record, want.mode)
        q = self.protocols.record_cpp_qualifier(record.name)
        n = escape_cpp_name(bare_name(record.name))
        if plan.default_ctor == want:
            out.write(f"\n{head}{inline}{q}::{n}() : {n}({NULL_PROTOCOL_ARG_CPP}) {{}}\n")
        if plan.ctor != want:
            return
        out.write("\n")
        self.ctx.emit_definition_echo(out, init.loc)
        if init.params:
            template_header, cpp_params = self._ctor_cpp_params(
                record, emit_defaults=False)
            # The class head, then the ctor's own (nested, never merged).
            out.write(f"{head}{template_header}{inline}{q}::{n}({cpp_params})")
        else:
            out.write(f"{head}{inline}{q}::{n}()")
        self._emit_ctor_tail(out, record, body_indent_level=1)

    def _gen_copy_ops(self, out: TextIO, record: TpyRecord,
                      *, mode: MethodEmitMode) -> None:
        """Copy ctor/assign delegating to __copy__ (plus defaulted moves when
        there is no __del__): declared in the struct (``mode="decl"``), defined
        at namespace scope -- the delegation constructs and destroys a
        temporary, which needs the members' complete types."""
        record_info = self.ctx.analyzer.registry.get_record(record.name)
        if not record_info or not record_info.has_copy:
            return
        n = escape_cpp_name(bare_name(record.name))
        if mode == "decl":
            out.write(f"{INDENT}// copyable via __copy__\n")
            out.write(f"{INDENT}{n}(const {n}& other);\n")
            out.write(f"{INDENT}{n}& operator=(const {n}& other);\n")
            if not record_info.has_del:
                out.write(f"{INDENT}{n}({n}&&) = default;\n")
                out.write(f"{INDENT}{n}& operator=({n}&&) = default;\n")
            return
        prefix = "".join(self._def_prefix(record, mode))
        q = self.protocols.record_cpp_qualifier(record.name)
        out.write("\n")
        out.write(f"{prefix}{q}::{n}(const {n}& other) : {n}(other.__copy__()) {{}}\n")
        out.write(f"{prefix}{q}& {q}::operator=(const {n}& other) {{\n")
        out.write(f"{INDENT}if (this != &other) {{ *this = other.__copy__(); }}\n")
        out.write(f"{INDENT}return *this;\n")
        out.write("}\n")

    # Picked by hand to match the spirit of GCC's ``max-inline-insns-auto``
    # default (30 insns) -- bodies above this go to .cpp; smaller bodies
    # stay inline in the .hpp so call sites can inline without LTO. The
    # insns/source-stmts ratio is rough; revisit with measurements if the
    # split feels wrong.
    _SMALL_METHOD_STMT_THRESHOLD = 7

    @classmethod
    def _stmt_count(cls, stmts: list[TpyStmt]) -> int:
        """Count statements, descending into nested control-flow bodies.
        Nested function definitions count as 1 -- their bodies live in their
        own scope and don't add to the enclosing method's complexity.
        """
        n = 0
        for stmt in stmts:
            n += 1
            for nested in stmt.sub_bodies():
                n += cls._stmt_count(nested)
        return n

    @classmethod
    def _method_body_is_small(cls, method: TpyFunction) -> bool:
        body = method.body
        start = 1 if body and is_docstring(body[0]) else 0
        return cls._stmt_count(body[start:]) <= cls._SMALL_METHOD_STMT_THRESHOLD

    def _member_def(self, sig: MemberSignature, *, small: bool = True,
                    variant: bool = False) -> MemberDef:
        return member_def(sig, cycle_member=bool(self.ctx.cycle_peers),
                          small=small, variant=variant)

    def _def_plan(self, record: TpyRecord) -> _RecordDefPlan | None:
        """Where each of `record`'s member bodies is defined; None for a
        native record. Every decl site, def site and `_genexpr_in_source`
        reads it."""
        if self._is_native(record):
            return None
        plan = self._def_plans.get(record)
        if plan is None:
            plan = self._def_plans[record] = self._record_def_plan(record)
        return plan

    def _record_def_plan(self, record: TpyRecord) -> _RecordDefPlan:
        generic = bool(record.type_params)
        # The synthesized members' signature: the class's template head only.
        class_sig = MemberSignature(template=generic, deduced=False)
        members: IdentityMap[TpyFunction, EmittedMember] = IdentityMap()
        methods: IdentityMap[TpyFunction, MemberDef] = IdentityMap()
        variants: IdentityMap[TpyFunction, MemberDef] = IdentityMap()
        # An overload group has one partition, keyed on its implementation;
        # the implementation's clone twin shares it.
        ctor = None
        destructor_small: bool | None = None
        twins = self.ctx.analyzer.overload_twins
        emission = record_emitted_members(
            record, self.ctx.analyzer.overload_groups, twins)
        for entry in emission.members:
            members[entry.func] = entry
            small = self._method_body_is_small(entry.body)
            if entry.kind is MemberKind.VARIANT:
                if entry.body not in methods:
                    methods[entry.body] = (
                        self._member_def(
                            MemberSignature(template=entry.sig.template,
                                            deduced=False),
                            small=small))
                variants[entry.func] = self._member_def(
                    entry.sig, small=small, variant=True)
            elif entry.kind in (MemberKind.METHOD, MemberKind.EXPANDED):
                methods[entry.func] = self._member_def(entry.sig, small=small)
            elif entry.kind is MemberKind.CTOR:
                ctor = self._member_def(entry.sig, small=small)
            elif entry.kind is MemberKind.DESTRUCTOR:
                # One partition for the family so it stays together.
                destructor_small = small and destructor_small is not False
            # A RESUMABLE member is defined with its frame
            # (`_emit_resumable_structs`).
        # A twin takes its implementation's partition, when that is emitted.
        for method in record.methods:
            impl = twins.get(method)
            if impl is not None and impl in methods:
                methods[method] = methods[impl]
        default_ctor = None
        if ctor is not None and self._ctor_all_protocols_optional(record):
            default_ctor = self._member_def(class_sig)
        record_info = self.ctx.analyzer.registry.get_record(record.name)
        copy_ops = None
        if record_info is not None and record_info.has_copy:
            copy_ops = self._member_def(class_sig)
        move_dtor = (None if destructor_small is None
                     else self._member_def(class_sig, small=destructor_small))
        return _RecordDefPlan(members=members, shims=emission.shims,
                              methods=methods,
                              variants=variants, ctor=ctor,
                              default_ctor=default_ctor, copy_ops=copy_ops,
                              move_dtor=move_dtor)

    def member_def_mode(self, member: TpyFunction,
                        record: TpyRecord) -> MethodEmitMode | None:
        """The partition `member`'s out-of-line definition lands in, or None
        when its body is in the struct or emits itself some other way.
        `member` is a method, the `__init__` or the `__del__` / `__move__`."""
        plan = self._def_plan(record)
        if plan is None:
            return None
        if member is record.init_method:
            entry = plan.ctor
        elif member is record.del_method or member is record.move_method:
            entry = plan.move_dtor
        else:
            stubs = self.ctx.analyzer.overload_groups.get(member)
            if stubs:
                # The group's partition only while no variant body is in
                # the struct.
                if any(plan.variants[s].mode == "inline" for s in stubs):
                    return None
            entry = plan.methods.get(member)
        if entry is None or entry.mode == "inline":
            return None
        return entry.mode

    def gen_record_method_defs(self, out: TextIO, record: TpyRecord,
                                *, mode: MethodEmitMode,
                                template: bool = False) -> None:
        if not self._is_native(record) and self._record_shadows_local_type(record):
            with qualify_shadowed_nominals():
                self._gen_record_method_defs(out, record, mode=mode,
                                             template=template)
        else:
            self._gen_record_method_defs(out, record, mode=mode,
                                         template=template)

    def gen_record_template_defs(self, out: TextIO, record: TpyRecord) -> None:
        """The out-of-class definitions of `record`'s template members, for
        the end of the header."""
        self.gen_record_method_defs(out, record, mode="def_hpp", template=True)

    def _gen_record_method_defs(self, out: TextIO, record: TpyRecord,
                                *, mode: MethodEmitMode, template: bool) -> None:
        """Emit the out-of-line member definitions of ``record`` at namespace
        scope whose plan entry is ``(mode, template)``: ``"def_hpp"`` bodies
        are ``inline`` in the header, ``"def_cpp"`` ones are in the source;
        ``template`` selects the template members (the end-of-header pass)
        over the rest.
        """
        plan = self._def_plan(record)
        if plan is None:
            return
        # Recurse into nested records first so a nested method's def sits next
        # to its outer container's defs in the file (matches struct emission
        # order).
        for nested in record.nested_records:
            self.gen_record_method_defs(out, nested, mode=mode, template=template)
        want = MemberDef(mode, template)
        # Out-of-line ctor + special-member definitions (declared in-struct by
        # gen_record_decl) come before the record's method defs, mirroring the
        # in-struct declaration order.
        self._gen_ctor_def(out, record, want=want)
        if plan.copy_ops == want:
            self._gen_copy_ops(out, record, mode=mode)
        if plan.move_dtor == want:
            self._gen_move_and_destructor(out, record, mode=mode)
        self._gen_record_methods(out, record, want)
        self._gen_shims(out, record, want)

    def _gen_resumable_method_decl(self, out: TextIO, method: TpyFunction,
                                   record: TpyRecord) -> None:
        """In-struct declaration of a generator / async method; the factory
        body and the frame struct land in `generator.py`'s post-struct emit
        pass."""
        if method.is_generator:
            # The resumable emitter's templated struct name, so a method on a
            # generic class spells the return type `__gen_Box_items<T>`.
            with self.gen_async._resumable_shape(ResumableShape.GENERATOR):
                struct_name = self.gen_async._struct_name_templated(
                    method, record.name)
        else:
            struct_name = self.gen_async._struct_name_templated(method, record.name)
        params = self.gen_async._emit_method_params_decl(
            method, record.name, emit_defaults=True)
        const_suffix = " const" if method.is_readonly else ""
        out.write("\n")
        # No record_name: the enclosing class template's `[T1, ...]` are in
        # scope here, so emit only the method's own type params /
        # proto-typed-param template args (or nothing).
        self.gen_async._emit_template_header(out, method, indent=INDENT)
        out.write(f"{INDENT}{struct_name} {method.name}({params}){const_suffix};\n")

    def _gen_record_methods(self, out: TextIO, record: TpyRecord,
                            want: MemberDef | None) -> None:
        """Emit the methods of `record`: with ``want`` None, the in-struct
        pass (a declaration, or the body of a member the plan keeps in the
        struct); else the definitions whose plan entry is ``want``."""
        plan = self._def_plan(record)
        assert plan is not None
        dynamic_overrides = self.functions._get_dynamic_override_info(record.name)
        for method in record.methods:
            member = plan.members.get(method)
            if (want is None and member is not None
                    and member.kind is MemberKind.RESUMABLE):
                self._gen_resumable_method_decl(out, method, record)
                continue
            entry = plan.methods.get(method)
            if entry is None:
                continue
            stubs = self.ctx.analyzer.overload_groups.get(method)
            if stubs:
                literal_only = self.functions._overload_stubs_are_literal_only(
                    stubs, method)
                emit_variant = (self.functions._gen_literal_specialized_method
                                if literal_only else
                                self.functions._gen_overload_specialized_method)
                for stub in stubs:
                    mode = self._emit_mode(plan.variants[stub], want)
                    if mode is not None:
                        emit_variant(
                            out, method, stub, record.name,
                            record_type_param_bounds=(
                                record.type_param_bounds or None),
                            dynamic_overrides=dynamic_overrides, mode=mode)
            elif method in self.ctx.analyzer.overload_twins:
                # Const clone of an @auto_readonly @overload impl: the mutable
                # clone emitted every stub's specialization, const ones included.
                continue
            else:
                mode = self._emit_mode(entry, want)
                if mode is not None:
                    self.functions.gen_method_def(
                        out, method, record.name, dynamic_overrides,
                        record_type_param_bounds=(
                            record.type_param_bounds or None),
                        mode=mode)

    @staticmethod
    def _emit_mode(entry: MemberDef,
                   want: MemberDef | None) -> MethodEmitMode | None:
        """How the pass selected by ``want`` (None: the in-struct pass) emits
        a member placed at ``entry``; None when another pass emits it."""
        if want is None:
            return "inline" if entry.mode == "inline" else "decl"
        return entry.mode if entry == want else None

    def _gen_nested_enum_decl(self, out: TextIO, enum: TpyEnum) -> None:
        """Generate an enum class declaration inside a parent struct."""
        enum_type = self.ctx.analyzer.registry.get_enum(enum.name)
        if not enum_type:
            return
        underlying = enum_info_of(enum_type).underlying_type.to_cpp()
        short_name = bare_name(enum.name)
        out.write(f"{INDENT}enum class {short_name} : {underlying} {{\n")
        for member_name, value, _ in enum.members:
            out.write(f"{INDENT}{INDENT}{member_name} = {value},\n")
        out.write(f"{INDENT}}};\n\n")

    def _record_has_str_repr(self, record_info: RecordInfo | None) -> tuple[bool, bool]:
        """(has_str, has_repr) for a record: whether the class in its MRO
        defining `__str__` / `__repr__` (`mro_method_definer`) is a TPy
        class, whose method the record's C++ answers as a member.

        Operator<< prefers __str__ then __repr__; if neither is found in the
        chain we fall back to the field-by-field default. A native class's
        `__str__` (BaseException's) is not one: an Exception subclass prints
        the default; a native `__repr__` free function is
        `_inherited_native_repr`'s.
        """
        if record_info is None:
            return False, False
        registry = self.ctx.analyzer.registry

        def tpy_defined(name: str) -> bool:
            found = registry.mro_method_definer(record_info, name)
            return found is not None and not found[1].is_native

        return tpy_defined("__str__"), tpy_defined("__repr__")

    def _inherited_native_repr(self, record_info: RecordInfo | None
                               ) -> 'TpyType | None':
        """The ancestor, as the record spells it, whose `__repr__` the
        record runs when that `__repr__` is a native free function (a
        builtin base's: `class Sub(dict[K, V])`), else None. C++ inherits no
        free function, so the record gets a member forwarding to it."""
        if record_info is None:
            return None
        found = self.ctx.analyzer.registry.mro_method_definer(
            record_info, "__repr__")
        if found is None or found[0] is None:
            return None
        anc_type, anc = found
        if not any(fi.native_function
                   for fi in anc.get_method_overloads("__repr__")):
            return None
        return anc_type

    def _shadows_inherited_repr_into(self, record_info: RecordInfo | None
                                     ) -> bool:
        """Whether the record defines its own TPy `__repr__` while an
        ancestor's C++ streams one (`_inherited_native_repr`)."""
        if record_info is None:
            return False
        registry = self.ctx.analyzer.registry
        found = registry.mro_method_definer(record_info, "__repr__")
        if found is None or found[0] is not None or found[1].is_native:
            return False
        return any(self._inherited_native_repr(anc) is not None
                   for _, anc in registry.iter_mro_entries(record_info))

    def _overrides_inherited_range(self, record: TpyRecord) -> bool:
        """Whether `record` iterates by a TPy `__iter__` while an ancestor
        it inherits a C++ range from iterates natively (`is_native_iterable`)."""
        registry = self.ctx.analyzer.registry
        record_info = registry.get_record(record.name)
        if record_info is None or is_native_iterable(record_owner(record_info), registry):
            return False
        return any(is_native_iterable(anc_type, registry)
                   for anc_type, _ in registry.iter_mro_entries(record_info))

    def _gen_record_ostream(
        self, out: TextIO, record: TpyRecord,
        has_str_repr: tuple[bool, bool] | None = None,
    ) -> None:
        """Generate operator<< overload for printing a record. ``has_str_repr``
        may be passed by callers that already computed the (has_str, has_repr)
        pair to avoid a second ancestor walk."""
        name = self.protocols.record_cpp_qualifier(record.name)

        if has_str_repr is None:
            record_info = self.ctx.analyzer.registry.get_record(record.name)
            has_str_repr = self._record_has_str_repr(record_info)
        has_str, _ = has_str_repr
        # operator<< follows Python's print(rec) semantics: __str__ first,
        # then __repr__ as fallback. When __str__ is available, ignore the
        # __repr__ bit so the body emits the str form.
        has_repr = False if has_str else has_str_repr[1]
        # For template structs, generate a template operator<<
        if record.type_params:
            # Build params respecting INT type params
            params_parts = []
            for i, p in enumerate(record.type_params):
                kind = record.type_param_kinds[i] if record.type_param_kinds and i < len(record.type_param_kinds) else TypeParamKind.TYPE
                if kind == TypeParamKind.INT:
                    params_parts.append(f"std::size_t {p}")
                else:
                    params_parts.append(f"typename {p}")
            params = ", ".join(params_parts)
            out.write(f"\ntemplate<{params}>\n")
        else:
            out.write("\n")
        out.write(f"inline std::ostream& operator<<(std::ostream& os, const {name}& obj) {{\n")

        if has_str:
            out.write(f"{INDENT}os << obj.__str__();\n")
        elif self._inherited_native_repr(
                self.ctx.analyzer.registry.get_record(record.name)) is not None:
            out.write(f"{INDENT}obj.__repr_into__(os);\n")
        elif has_repr:
            out.write(f"{INDENT}os << obj.__repr__();\n")
        else:
            out.write(f'{INDENT}::tpy::print_object_default(os, "{record.name}", obj);\n')

        out.write(f"{INDENT}return os;\n")
        out.write("}\n")

    def _gen_move_and_destructor(self, out: TextIO, record: TpyRecord,
                                 *, mode: MethodEmitMode) -> None:
        """Generate a C++ destructor with drop-flag protection from a __del__ method.

        Emits:
        1. Custom move constructor that sets source's __tpy_owned_ = false
        2. Custom move assignment via destroy-and-reconstruct (runs destructor on
           old value, then placement-new move-constructs the new value)
        3. Destructor guarded by __tpy_owned_ to skip body on moved-from objects

        super().__del__() calls are dropped -- parent destructors are called
        automatically by C++ after the child destructor body runs.

        ``mode`` follows the method decl/def split: declared in the struct
        (``decl``), defined at namespace scope (the plan's ``move_dtor``
        partition) where mutually-recursive sibling member types are complete.
        """
        del_method = record.del_method
        if del_method is None:
            return

        name = record.name
        cpp_name = escape_cpp_name(bare_name(name))
        record_info = self.ctx.analyzer.registry.get_record(name)
        move_method = record.move_method
        nonmovable = (record_info is not None and not record.type_params
                      and not record_info.is_movable)

        is_def_mode = mode != "decl"
        prefix = "".join(self._def_prefix(record, mode)) if is_def_mode else ""
        q = self.protocols.record_cpp_qualifier(name)

        # Filter out super().__del__() calls -- they're automatic in C++
        body_stmts = [s for s in del_method.body if not is_super_del_call(s)]

        # --- Custom move constructor ---
        if move_method is not None:
            # Owner-declared relocating move: a member-wise move would hit a
            # member whose own move is unavailable (e.g. UninitArrayStorage for
            # a non-trivially-relocatable element, which can't relocate without
            # the owner's liveness). The dest's members are value-initialized
            # (empty) so the __move__ body relocates into a clean slate.
            src_name = move_method.params[0][0] if move_method.params else "other"
            cpp_src = escape_cpp_name(src_name)
            if mode == "decl":
                out.write(f"{INDENT}{cpp_name}({cpp_name}&& {cpp_src}) noexcept;\n")
            else:
                vinit_parts = []
                if record_info:
                    for p in record_info.parents:
                        vinit_parts.append(f"{record_base_cpp(record_info, p)}()")
                for fld in record.fields:
                    vinit_parts.append(f"{escape_cpp_name(fld.name)}()")
                vinit_list = emit_prims.member_init_list(vinit_parts, "")
                out.write(f"\n{prefix}{q}::{cpp_name}({cpp_name}&& {cpp_src}) noexcept{vinit_list} {{\n")
                # A scope cannot open before the member value-inits above, so
                # a check point a field's default constructor reaches is not
                # deferred (BUGS.md#move-member-init-check-point-fatal).
                defer = emit_prims.defer_signals_cpp(
                    record_info.get_method("__move__") if record_info else None)
                if defer is not None:
                    out.write(f"{INDENT}{defer}\n")
                self.functions.gen_body(out, move_method,
                                        indent_level=1)
                out.write(f"{INDENT}{cpp_src}.__tpy_owned_ = false;\n")
                out.write("}\n")
        elif nonmovable:
            # Non-generic class with a non-movable member and no __move__: a
            # member-wise move ctor would be ill-formed (it would move the
            # deleted-move member), and unlike a generic it is instantiated
            # eagerly, so emit an explicitly deleted move. Sema rejects actual
            # relocations of this type with a clean diagnostic before codegen;
            # construct-and-use-in-place and elided prvalue returns still work.
            if not is_def_mode:
                out.write(f"{INDENT}{cpp_name}({cpp_name}&&) = delete;\n")
                out.write(f"{INDENT}{cpp_name}& operator=({cpp_name}&&) = delete;\n")
        elif mode == "decl":
            out.write(f"{INDENT}{cpp_name}({cpp_name}&& other) noexcept;\n")
        else:
            init_parts = []
            if record_info:
                for p in record_info.parents:
                    init_parts.append(f"{record_base_cpp(record_info, p)}(std::move(other))")
            for fld in record.fields:
                cpp_fld = escape_cpp_name(fld.name)
                init_parts.append(f"{cpp_fld}(std::move(other.{cpp_fld}))")

            init_list = emit_prims.member_init_list(init_parts, "")

            out.write(f"\n{prefix}{q}::{cpp_name}({cpp_name}&& other) noexcept{init_list} {{\n")
            out.write(f"{INDENT}other.__tpy_owned_ = false;\n")
            out.write("}\n")

        # --- Custom move assignment (destroy-and-reconstruct) ---
        # The nonmovable branch already emitted a deleted move-assign above.
        if not nonmovable:
            if mode == "decl":
                out.write(f"{INDENT}{cpp_name}& operator=({cpp_name}&& other) noexcept;\n")
            else:
                out.write(f"{prefix}{q}& {q}::operator=({cpp_name}&& other) noexcept {{\n")
                out.write(f"{INDENT}if (this != &other) {{\n")
                out.write(f"{INDENT}{INDENT}this->~{cpp_name}();\n")
                out.write(f"{INDENT}{INDENT}new (this) {cpp_name}(std::move(other));\n")
                out.write(f"{INDENT}}}\n")
                out.write(f"{INDENT}return *this;\n")
                out.write("}\n")

        # --- Destructor with drop-flag guard ---
        out.write("\n")
        if mode == "decl":
            self.ctx.emit_declaration_echo(out, del_method.loc, INDENT)
            out.write(f"{INDENT}~{cpp_name}();\n")
            return
        self.ctx.emit_definition_echo(out, del_method.loc)
        out.write(f"{prefix}{q}::~{cpp_name}() {{\n")
        out.write(f"{INDENT}if (!this->__tpy_owned_) return;\n")
        if body_stmts:
            # A C++ destructor is noexcept: a `with`/`try`/`raise` in the body
            # emits a literal `throw` that would std::terminate (a
            # -Werror=terminate compile error). Wrap only those bodies so the
            # escaping exception is reported and the process aborts (fail-fast;
            # `report_del_exception` is [[noreturn]]) -- matching C++'s own
            # noexcept-destructor rule, not CPython's print-and-continue. A body
            # with no such construct emits no literal throw, so a plain
            # destructor stays wrap-free (and a throwing call there aborts at
            # the noexcept boundary just the same).
            wrap = _body_has_literal_throw(body_stmts)
            defer = emit_prims.defer_signals_cpp(
                record_info.get_method("__del__") if record_info else None)
            if defer is not None:
                out.write(f"{INDENT}{defer}\n")
            if wrap:
                out.write(f"{INDENT}try {{\n")
            self.functions.gen_body(
                out, del_method,
                indent_level=2 if wrap else 1)
            if wrap:
                out.write(f"{INDENT}}} catch (const std::exception& __del_exc) {{\n")
                out.write(f"{INDENT}{INDENT}::tpy::report_del_exception(__del_exc);\n")
                out.write(f"{INDENT}}} catch (...) {{\n")
                out.write(f"{INDENT}{INDENT}::tpy::report_del_exception();\n")
                out.write(f"{INDENT}}}\n")
        out.write("}\n")

    def _gen_covariant_converting_ctor(self, out: TextIO, record: TpyRecord) -> None:
        """Generate converting move constructor for covariant generic types.

        For Box[T] with Covariant[T], emits a template constructor that accepts
        Box<U>&& when U inherits T, enabling Box[Child] -> Box[Parent] conversion.
        """
        record_info = self.ctx.analyzer.registry.get_record(record.name)
        if not record_info or not record_info.type_params:
            return
        covariant = get_covariant_params(record_info)
        if not covariant:
            return

        short_name = bare_name(record.name)
        cpp_name = escape_cpp_name(short_name)

        # Build template params and requires clause
        other_params = []
        requires_parts = []
        for i, tp in enumerate(record_info.type_params):
            kind = (record_info.type_param_kinds[i]
                    if record_info.type_param_kinds and i < len(record_info.type_param_kinds)
                    else TypeParamKind.TYPE)
            u_name = f"__CovU_{tp}"
            if kind == TypeParamKind.INT:
                other_params.append(f"std::size_t {u_name}")
            else:
                other_params.append(f"typename {u_name}")
            if kind == TypeParamKind.TYPE:
                if tp in covariant:
                    requires_parts.append(f"std::is_base_of_v<{tp}, {u_name}>")
                else:
                    requires_parts.append(f"std::is_same_v<{tp}, {u_name}>")

        if not requires_parts:
            return

        other_type_args = ", ".join(
            f"__CovU_{tp}" for tp in record_info.type_params
        )
        template_str = ", ".join(other_params)
        requires_str = " && ".join(requires_parts)

        # Build member init list: transfer each field from __other
        init_parts = []
        for p in record_info.parents:
            parent_cpp = record_base_cpp(record_info, p, self.types.type_to_cpp)
            init_parts.append(f"{parent_cpp}(std::move(__other))")
        for fld in record.fields:
            cpp_fld = escape_cpp_name(fld.name)
            init_parts.append(f"{cpp_fld}(std::move(__other.{cpp_fld}))")
        init_list = emit_prims.member_init_list(init_parts, INDENT)

        out.write(f"\n{INDENT}template<{template_str}>\n")
        out.write(f"{INDENT}{INDENT}requires ({requires_str})\n")
        out.write(f"{INDENT}{cpp_name}({cpp_name}<{other_type_args}>&& __other) noexcept{init_list} {{\n")
        if record_info.has_del:
            out.write(f"{INDENT}{INDENT}__other.__tpy_owned_ = false;\n")
        out.write(f"{INDENT}}}\n")

        # Friend declaration for cross-instantiation member access
        friend_tparams = ", ".join("typename" for _ in record_info.type_params)
        out.write(f"{INDENT}template<{friend_tparams}> friend struct {cpp_name};\n")

    def _render_enum_member_default(self, expr) -> str | None:
        """C++ for a validated enum-member / class-constant field default
        (`Color.RED` -> `tpyapp::mod::Color::RED`), or None when the expr
        isn't that shape (registration would have rejected it)."""
        if not isinstance(expr, TpyFieldAccess):
            return None
        return type_member_default_cpp(self.ctx.analyzer, expr)

    def _gen_shims(self, out: TextIO, record: TpyRecord,
                   want: MemberDef | None) -> None:
        """The plan's delegating shims in their struct order: with ``want``
        None, the in-struct pass (a declaration, or the body of a shim kept
        in the struct); else the definitions placed at ``want``."""
        plan = self._def_plan(record)
        assert plan is not None
        for shim in plan.shims:
            if shim.kind.friend:
                # A hidden friend is defined in the struct.
                if want is None:
                    self._gen_friend_operator(out, record, shim)
            elif shim.kind is ShimKind.SUBSCRIPT:
                self._gen_subscript_operators(out, record, shim, want)
            elif shim.kind is ShimKind.DEREF:
                self._gen_deref_operators(out, record, shim, want)
            elif shim.kind is ShimKind.SIZE:
                self._gen_size_method(out, record, shim.targets[0], want)
            else:
                self._gen_call_operator(out, record, shim.targets[0], want)

    def _shim_mode(self, target: EmittedMember,
                   want: MemberDef | None) -> MethodEmitMode | None:
        """How the pass ``want`` emits a member shim forwarding to
        ``target``: placed where the target would be as a one-line body."""
        return self._emit_mode(
            self._member_def(target.sig, variant=target.variant), want)

    def _gen_subscript_operators(self, out: TextIO, record: TpyRecord,
                                 shim: Shim, want: MemberDef | None) -> None:
        """operator[] over the `__getitem__` defs sharing one index type
        (`@overload` stubs, `@dispatch` variants or plain defs): an
        @auto_readonly clone pair emits const first then mutable, a single
        def emits by its own const-ness. Writes go through
        ::tpy::__setitem__."""
        pair = _split_readonly_clone_pair(shim.targets)
        if pair is not None:
            const_target, mutable_target = pair
            # Clone pair: const operator first, then mutable -- a pair's
            # results differ by the receiver's access wherever it marks
            # one (a `Ptr[auto_readonly[T]]` pointee included).
            self._gen_subscript_operator(out, record, const_target, want,
                                         const=True)
            self._gen_subscript_operator(out, record, mutable_target, want,
                                         const=False)
            return
        for target in shim.targets:
            method = target.func
            if method.is_readonly:
                self._gen_subscript_operator(out, record, target, want,
                                             const=True)
                # Non-const overload only when return could be a reference
                needs_dual = (not method.return_type.is_value_type()
                              or isinstance(method.return_type, TypeParamRef))
                if needs_dual:
                    self._gen_subscript_operator(out, record, target, want,
                                                 const=False)
            else:
                self._gen_subscript_operator(out, record, target, want,
                                             const=False)

    def _gen_subscript_operator(self, out: TextIO, record: TpyRecord,
                                target: EmittedMember,
                                want: MemberDef | None, *,
                                const: bool) -> None:
        """One operator[] overload delegating to __getitem__. The non-const
        one's target is the mutable clone of an auto_readonly pair, a
        genuinely mutable single __getitem__, or -- for the single-readonly
        dual -- the readonly method itself, whose return the delegation hands
        back."""
        mode = self._shim_mode(target, want)
        if mode is None:
            return
        method = target.func
        index_param_name, index_type = method.params[0]
        # Mirror __getitem__'s emitted key param (borrow form + the method's
        # inferred const-ness), like the call operator -- not the storage form.
        index_cpp = self.functions.gen_shim_params(method, record.name)
        ret_cpp = self.functions.shim_return_cpp(method)
        index_arg = forward_param(escape_cpp_name(index_param_name), index_type)
        self._emit_forwarder(out, record, mode, ret_cpp, "operator[]",
                             f"({index_cpp}){' const' if const else ''}",
                             [f"return __getitem__({index_arg});"])

    def _emit_forwarder(self, out: TextIO, record: TpyRecord,
                        mode: MethodEmitMode, ret: str, name: str, tail: str,
                        body: list[str], *, one_line: bool = False) -> None:
        """One delegating member: its body in the struct ("inline"), its
        declaration there ("decl"), or its out-of-class definition
        ("def_hpp" / "def_cpp"). ``tail`` is the params and qualifiers after
        the name."""
        if mode == "decl":
            out.write(f"\n{INDENT}{ret} {name}{tail};\n")
            return
        if mode == "inline":
            out.write(f"\n{INDENT}{ret} {name}{tail}")
            ind = INDENT
        else:
            head, inline = self._def_prefix(record, mode)
            q = self.protocols.record_cpp_qualifier(record.name)
            out.write(f"\n{head}{inline}{ret} {q}::{name}{tail}")
            ind = ""
        if one_line:
            out.write(f" {{ {body[0]} }}\n")
            return
        out.write(" {\n")
        for line in body:
            out.write(f"{ind}{INDENT}{line}\n")
        out.write(f"{ind}}}\n")

    def _gen_friend_operator(self, out: TextIO, record: TpyRecord,
                             shim: Shim) -> None:
        """A hidden friend operator delegating to its dunder (binary,
        reflected or unary): it lets user records conform to C++ concepts
        written with operator syntax (`t + other`, `t < other`). Defined in
        the struct, where only argument-dependent lookup finds it -- an
        operator visible to ordinary lookup would make a class's `!=` turn
        off C++20's reversed `==`."""
        method = shim.targets[0].func
        ret_cpp = self._friend_return_cpp(method, record)
        operand = f"const {escape_cpp_name(bare_name(record.name))}&"
        if shim.kind is ShimKind.UNARY:
            out.write(f"\n{INDENT}friend {ret_cpp} operator{shim.op}({operand} operand) {{\n")
            out.write(f"{INDENT}{INDENT}return operand.{method.name}();\n")
            out.write(f"{INDENT}}}\n")
            return
        param_name, param_type = method.params[0]
        # The method's own param spelling (its const verdict), which the
        # delegation must bind.
        param_cpp = self.functions.gen_shim_params(method, record.name)
        arg = forward_param(escape_cpp_name(param_name), param_type)
        if shim.kind is ShimKind.BINARY:
            # `record OP other`: the record on the left.
            out.write(f"\n{INDENT}friend {ret_cpp} operator{shim.op}({operand} lhs, {param_cpp}) {{\n")
            out.write(f"{INDENT}{INDENT}return lhs.{method.name}({arg});\n")
        else:
            # `other OP record`: the record on the RIGHT, delegating to
            # __rOP__. Matches DUNDER_CPP_TEMPLATES' reverse entries
            # (`({0}) OP ({self})`), which sema emits when it resolves a
            # binop to the reverse method.
            out.write(f"\n{INDENT}friend {ret_cpp} operator{shim.op}({param_cpp}, {operand} rhs) {{\n")
            out.write(f"{INDENT}{INDENT}return rhs.{method.name}({arg});\n")
        out.write(f"{INDENT}}}\n")

    def _friend_return_cpp(self, method: TpyFunction, record: TpyRecord) -> str:
        """A hidden friend operator's return. The friend's body stays in the
        class; in a class template a spelled return naming a class declared
        later would need it complete there, while `decltype(auto)` is
        dependent and keeps a reference result a reference."""
        if record.type_params:
            return "decltype(auto)"
        return self.functions.shim_return_cpp(method)

    def _gen_call_operator(self, out: TextIO, record: TpyRecord,
                           target: EmittedMember,
                           want: MemberDef | None) -> None:
        """operator() delegating to __call__ (callable objects): enables
        obj(args) syntax and std::invocable concept conformance."""
        mode = self._shim_mode(target, want)
        if mode is None:
            return
        method = target.func
        # Mirror the method emit for both axes: its return and the
        # inferred param const-ness (a mutated param stays T& in the
        # method; a const shim param wouldn't bind to it).
        ret_cpp = self.functions.shim_return_cpp(method)
        const_suffix = " const" if method.is_readonly else ""
        params_cpp = self.functions.gen_shim_params(method, record.name)
        arg_names = ", ".join(forward_param(escape_cpp_name(p_name), p_type)
                              for p_name, p_type in method.params)
        self._emit_forwarder(out, record, mode, ret_cpp, "operator()",
                             f"({params_cpp}){const_suffix}",
                             [f"return __call__({arg_names});"])

    def _gen_deref_operators(self, out: TextIO, record: TpyRecord,
                             shim: Shim, want: MemberDef | None) -> None:
        """operator*() for types with __deref__ (C++ interop: *box instead
        of box.__deref__()); Ptr[T] maps to a raw T*, which has it already.

        Mirrors the __getitem__ dual-overload pattern: an @auto_readonly
        __deref__ clone pair (mutable + const) gets both operator*() and
        operator*() const so const receivers can dereference. A single
        __deref__ keeps the single overload.
        """
        pair = _split_readonly_clone_pair(shim.targets)
        if pair is not None:
            const_target, mutable_target = pair
            self._emit_operator_star(out, record, mutable_target, want)
            self._emit_operator_star(out, record, const_target, want,
                                     qual=" const")
            return
        self._emit_operator_star(out, record, shim.targets[0], want)

    def _emit_operator_star(self, out: TextIO, record: TpyRecord,
                            target: EmittedMember, want: MemberDef | None,
                            qual: str = "") -> None:
        mode = self._shim_mode(target, want)
        if mode is None:
            return
        self._emit_forwarder(out, record, mode, "auto", "operator*",
                             f"(){qual} -> decltype(__deref__())",
                             ["return __deref__();"])

    def _gen_size_method(self, out: TextIO, record: TpyRecord,
                         target: EmittedMember,
                         want: MemberDef | None) -> None:
        """size() from __len__ for STL compatibility: user types with
        __len__ work with C++ algorithms and container concepts."""
        mode = self._shim_mode(target, want)
        if mode is None:
            return
        len_method = target.func
        ret_cpp = len_method.return_type.to_cpp()
        ret_type = len_method.return_type
        ret_int_tr = int_traits_of(ret_type)
        # BigInt is handled by its own branch below; here is_signed covers only
        # signed fixed-width ints (which static_cast<size_t> handles directly).
        is_signed = ret_int_tr is not None and ret_int_tr.signed
        if ret_cpp == "size_t":
            body, one_line = ["return __len__();"], True
        elif is_big_int_type(ret_type):
            # BigInt has no size_t cast operator (only checked extractors), so
            # a plain static_cast<size_t> would fail to compile; to_size_checked
            # also rejects a negative / out-of-size_t result.
            body, one_line = [
                "size_t __sz;",
                "if (!__len__().to_size_checked(__sz)) ::tpy::raise<::tpy::ValueError>(\"__len__() should return >= 0\");",
                "return __sz;",
            ], False
        elif is_signed:
            body, one_line = [
                "auto len = __len__();",
                "if (len < 0) ::tpy::raise<::tpy::ValueError>(\"__len__() should return >= 0\");",
                "return static_cast<size_t>(len);",
            ], False
        else:
            body, one_line = ["return static_cast<size_t>(__len__());"], True
        self._emit_forwarder(out, record, mode, "size_t", "size", "() const",
                             body, one_line=one_line)
