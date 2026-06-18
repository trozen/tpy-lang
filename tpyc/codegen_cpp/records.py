"""
TurboPython Record Code Generation

Generates C++ structs from TurboPython records.
"""

from __future__ import annotations
import io
from collections import defaultdict
from typing import TextIO, TYPE_CHECKING

from ..typesys import (
    TpyType, NominalType, OptionalType, OwnType, ReadonlyType,
    AutoReadonlyType, AutoOwnType, FinalType, ClassVarType,
    TypeParamRef, TypeParamKind, RecordInfo, TupleType, UnionType,
    unwrap_readonly, unwrap_optional_own,
    get_covariant_params, PtrType,
    bare_name,
    del_suppresses_default_ctor, type_value_init_indeterminate,
)
from ..parse import (
    TpyRecord, TpyEnum, TpyFunction, TpyStmt, TpyExprStmt, TpyAssign,
    TpyMethodCall, TpyFieldAccess, TpyName, TpyCoerce, TpyNestedDef,
    TpyPassStmt, SourceLocation,
    is_docstring, is_super_del_call, is_base_init_call,
    collect_name_refs, collect_top_level_local_names, expr_reads_self_field,
)
from ..namespace import Namespace

from .. import qnames
from .context import INDENT, DUNDER_TO_BINARY_OP, CodeGenError, escape_cpp_name, enum_cpp_name
from .functions import factory_default_to_cpp
from .resumable_cfg import ResumableShape
from ..type_def_registry import (
    is_span_iter, is_array,
    is_big_int_type, is_bytes_type, int_traits_of,
    is_enum_type, enum_info_of, protocol_info_of,
    is_set, is_dict,
)

if TYPE_CHECKING:
    from .context import CodeGenContext
    from .types import TypeResolver
    from .expressions import ExpressionGenerator
    from .functions import FunctionGenerator, MethodEmitMode
    from .gen_async import AsyncCoroCodegen
    from .gen_generators import GeneratorCodegen
    from .protocols import ProtocolGenerator


def _split_readonly_clone_pair(methods: list) -> tuple | None:
    """Return (const_clone, mutable_clone) when `methods` is exactly an
    @auto_readonly clone pair, otherwise None.

    `methods` may be any list of TpyFunction-like objects with `is_readonly`
    (e.g. the impls of a single dunder name, or a group of @overload stubs
    sharing a parameter type). Used by operator[] and operator*() emission
    to decide between dual-overload and single-overload paths.
    """
    if len(methods) != 2:
        return None
    const = next((m for m in methods if m.is_readonly), None)
    mutable = next((m for m in methods if not m.is_readonly), None)
    if const is None or mutable is None:
        return None
    return const, mutable


class RecordGenerator:
    """Generates C++ structs from TurboPython records."""

    def __init__(
        self,
        ctx: CodeGenContext,
        types: TypeResolver,
        protocols: ProtocolGenerator,
        expressions: ExpressionGenerator,
        functions: FunctionGenerator,
    ):
        self.ctx = ctx
        self.types = types
        self.protocols = protocols
        self.expressions = expressions
        self.functions = functions
        self.gen_generators: GeneratorCodegen  # Set by CodeGenerator after init
        self.gen_async: AsyncCoroCodegen  # Set by CodeGenerator after init

    def sort_records_by_inheritance(self, records: list[TpyRecord]) -> list[TpyRecord]:
        """Sort records so parent classes come before children.

        Uses topological sort based on inheritance relationships AND
        hashed-container-element relationships: a record `Holder` with a
        field `Own[set[Point]]` must emit AFTER `Point` so that
        `std::hash<Point>` (emitted right after Point's struct def) is
        available when `Holder`'s struct uses `ordered_set<Point>`.
        Native records are excluded (no C++ struct generation needed).
        """
        # Filter out native records -- they don't generate C++ structs
        records = [r for r in records if not self._is_native(r)]
        record_by_name = {r.name: r for r in records}

        # Build dependency graph
        # Only consider user-defined parents (NominalType records), not builtin types
        dependencies: dict[str, set[str]] = {r.name: set() for r in records}
        for record in records:
            record_info = self.ctx.analyzer.registry.get_record(record.name)
            if record_info is None:
                continue
            for p in record_info.parents:
                if (isinstance(p, NominalType) and p.is_user_record
                        and p.name in record_by_name):
                    dependencies[record.name].add(p.name)
            # Hashed-container element dependency: walk each field's type
            # peeling wrappers, collect user-record key types from any
            # nested set/dict so std::hash<K> is emitted before this
            # record's struct uses ordered_set<K> / ordered_map<K, V>.
            for fld in record_info.fields:
                if fld.type is None:
                    continue
                hash_deps: set[str] = set()
                self._collect_hash_key_records(fld.type, hash_deps)
                for dep in hash_deps:
                    if dep in record_by_name and dep != record.name:
                        dependencies[record.name].add(dep)

        # Topological sort (Kahn's algorithm)
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

        # If any records are left (circular dependency), add them at the end
        for record in records:
            if record.name not in result_names:
                result.append(record)

        return result

    def _collect_hash_key_records(self, typ: TpyType, deps: set[str]) -> None:
        """Walk a type peeling wrappers; for every `set[K]` / `dict[K, V]`
        found, collect K's user-record name into `deps` so the topo sort
        can emit K's struct (and its `std::hash<K>` specialization) before
        the record using the container.
        """
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

    def gen_record_decl(self, out: TextIO, record: TpyRecord) -> None:
        """Generate a struct declaration for a record."""
        # Native records don't generate C++ structs -- they're defined in external headers
        if self._is_native(record):
            return

        # Get record info for inheritance information
        record_info = self.ctx.analyzer.registry.get_record(record.name)

        # Emit class header as source comments (preceding comments + class line)
        if record.loc:
            self.ctx.emit_preceding_comments(out, record.loc)
            self.ctx.emit_source_comment(out, record.loc)

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
                bases.append(p.to_cpp())
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
        for nested_rec in record.nested_records:
            buf = io.StringIO()
            self.gen_record_decl(buf, nested_rec)
            for line in buf.getvalue().splitlines(True):
                out.write(INDENT + line if line.strip() else line)
            out.write("\n")

        # Fields
        for fld in record.fields:
            self.ctx.emit_preceding_comments(out, fld.loc, indent=INDENT)
            self.ctx.emit_source_comment(out, fld.loc, indent=INDENT)
            cpp_type = self.types.type_to_cpp(fld.type)
            default = ""
            if fld.default_value is not None:
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
                # gen_expr path (which needs resolved facts) can't be used.
                init = self._render_enum_member_default(fld.default_expr)
                if init is not None:
                    default = f" = {init}"
            elif fld.is_factory_default:
                default = f" = {factory_default_to_cpp(fld.type)}"
            out.write(f"{INDENT}{cpp_type} {escape_cpp_name(fld.name)}{default};\n")

        # @native records skip emission -- the user's header owns the storage.
        # Use the expression generator so tuples / type-coerced literals emit
        # the same way module-level Final constants do.
        if record_info and not record_info.is_native:
            for cc_name, cc_fld in record_info.class_constants.items():
                self.ctx.emit_preceding_comments(out, cc_fld.loc, indent=INDENT)
                self.ctx.emit_source_comment(out, cc_fld.loc, indent=INDENT)
                cpp_type = self.types.type_to_cpp(cc_fld.type)
                if cc_fld.default_expr is not None:
                    init = self.expressions.gen_expr(cc_fld.default_expr, cc_fld.type)
                else:
                    init = cc_fld.default_value if cc_fld.default_value is not None else "{}"
                is_final = record_info.is_final_class_constant(cc_name)
                storage = "static constexpr" if is_final else "static inline"
                out.write(f"{INDENT}{storage} {cpp_type} {escape_cpp_name(cc_name)} = {init};\n")

        # Drop flag for classes with __del__ -- prevents double-drop after move.
        # Only emit on the root class; children inherit the parent's flag so that
        # all destructors in the chain check the same field.
        if record.del_method is not None and not self.ctx.any_ancestor_has_del(record.name):
            out.write(f"{INDENT}bool __tpy_owned_ = true;\n")

        out.write("\n")

        # Determine constructor generation strategy
        if record.init_method:
            has_params = bool(record.init_method.params)
            saved_func_params = self.ctx.current_func_params
            saved_in_method = self.ctx.in_method
            saved_ns = self.ctx.current_ns
            self.ctx.current_func_params = {
                pname: ptype for pname, ptype in record.init_method.params}
            self.ctx.in_method = True
            # Field-init RHS expressions live lexically in the constructor
            # body, so binding-based dispatch in expression codegen needs the
            # constructor's namespace. The non-init body gets the same
            # namespace handed to gen_body further down.
            self.ctx.current_ns = self._build_init_local_ns(record)
            base_inits = self._extract_base_inits(record.init_method, record)
            inits, hoisted_ids = self._extract_field_inits(record.init_method, record)
            self.ctx.current_func_params = saved_func_params
            self.ctx.in_method = saved_in_method
            self.ctx.current_ns = saved_ns
            non_init_stmts = self._get_non_init_stmts(record.init_method, record, hoisted_ids)

            self.ctx.emit_preceding_comments(out, record.init_method.loc, indent=INDENT)
            self.ctx.emit_source_comment(out, record.init_method.loc, indent=INDENT)
            if has_params:
                init_defaults = record.init_method.defaults if record.init_method.defaults else None
                has_required_params = not init_defaults or any(d is None for d in init_defaults)

                # Generate parameterized constructor from __init__
                proto_params = self.functions.protocols.get_all_protocol_params(
                    record.init_method.params)
                has_dynamic = self.functions._has_dynamic_protocol_params(
                    record.init_method.params)

                # Default constructor: when all params have defaults and every
                # protocol param is optional, delegate to the template ctor with
                # nullptr so the __init__ body executes (else branch runs via
                # if constexpr). Otherwise use = default.
                all_protocols_optional = (
                    proto_params
                    and not has_required_params
                    and all(p.has_none for p in proto_params))
                if has_required_params or proto_params:
                    if all_protocols_optional:
                        out.write(f"{INDENT}{cpp_rec_name}() : {cpp_rec_name}(static_cast<std::nullptr_t*>(nullptr)) {{}}\n")
                    elif (record_info is not None
                          and self._all_fields_default_constructible(record)
                          and not del_suppresses_default_ctor(record_info)):
                        out.write(f"{INDENT}{cpp_rec_name}() = default;\n")
                # Ctor params that the body mutates (typically via value->Ptr
                # coercion at a callee site or assignment to a Ptr storage)
                # must drop the perf-default `const T&` -- the address-take
                # produces `T*`, not `const T*`. Regular methods plumb this
                # through gen_params; constructors used to skip it because
                # ctors must accept temporaries, but a mutated param can't
                # accept a temporary anyway (sema's requires_mutable_lvalue
                # rejects the rvalue call before reaching codegen).
                init_mp = self.functions._get_method_mutated_params(
                    record.init_method, record.name)
                if proto_params or has_dynamic:
                    cpp_params = self.functions.gen_params_with_protocols(
                        record.init_method.params,
                        record.init_method.type_params,
                        const_params=True,
                        mutated_params=init_mp,
                        defaults=init_defaults,
                        emit_defaults=True,
                    )
                    if proto_params:
                        template_header = self.functions.protocols.gen_combined_template_header(
                            record.init_method.type_params or [], proto_params,
                            record.type_param_bounds or None,
                        )
                        out.write(f"{INDENT}{template_header}")
                else:
                    init_ae = self.functions._get_method_addr_escapes(
                        record.init_method, record.name)
                    cpp_params = self.functions.gen_params(
                        record.init_method.params,
                        record.init_method.type_params,
                        const_params=True,
                        mutated_params=init_mp,
                        addr_escapes_params=init_ae,
                        defaults=init_defaults,
                        emit_defaults=True,
                        class_type_params=set(record.type_params) if record.type_params else None,
                    )
                out.write(f"{INDENT}explicit {cpp_rec_name}({cpp_params})")
            else:
                # No params: generate default constructor with body
                out.write(f"{INDENT}{cpp_rec_name}()")

            # Build member init list: base inits (if any) + field inits
            all_inits = list(base_inits)
            all_inits.extend(f"{escape_cpp_name(name)}({val})" for name, val in inits)
            if all_inits:
                out.write(" : ")
                out.write(", ".join(all_inits))
            if non_init_stmts:
                out.write(" {\n")
                local_ns = self._build_init_local_ns(record)
                self.functions.gen_body(out, non_init_stmts, record.init_method.params,
                                         record.init_method.return_type, record.init_method,
                                         local_ns, indent_level=2, is_method=True,
                                         record_type_param_bounds=record.type_param_bounds or None,
                                         owning_record_name=record.name)
                out.write(f"{INDENT}}}\n")
            else:
                out.write(" {}\n")
        else:
            # No __init__: plain records stay C++ aggregates (no ctor declared).
            # Records with __del__/@nocopy/__copy__ get user-declared copy/move
            # ops, which suppress the implicit default ctor -- restore it with
            # `= default;` unless `del_suppresses_default_ctor` says otherwise.
            if record_info and record_info.inherits_init_from is not None:
                # `using Foo::Foo` only compiles if both halves match the C++
                # class name. @native renames let the Python and C++ short
                # names diverge, so split on the C++ side, not parent.name.
                parent_cpp = record_info.inherits_init_from.to_cpp()
                parent_cpp_short = parent_cpp.rsplit("::", 1)[-1]
                out.write(f"{INDENT}using {parent_cpp}::{parent_cpp_short};\n")
            if record_info and (record_info.is_nocopy or record_info.has_del or record_info.has_copy):
                if not del_suppresses_default_ctor(record_info):
                    out.write(f"{INDENT}{cpp_rec_name}() = default;\n")

        # Copy/move ops for @nocopy, __del__, or __copy__ classes.
        if record_info and record_info.has_copy:
            n = cpp_rec_name
            out.write(f"{INDENT}// copyable via __copy__\n")
            out.write(f"{INDENT}{n}(const {n}& other) : {n}(other.__copy__()) {{}}\n")
            out.write(f"{INDENT}{n}& operator=(const {n}& other) {{\n")
            out.write(f"{INDENT}{INDENT}if (this != &other) {{ *this = other.__copy__(); }}\n")
            out.write(f"{INDENT}{INDENT}return *this;\n")
            out.write(f"{INDENT}}}\n")
            if not record_info.has_del:
                out.write(f"{INDENT}{n}({n}&&) = default;\n")
                out.write(f"{INDENT}{n}& operator=({n}&&) = default;\n")
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
        self._gen_move_and_destructor(out, record)

        # Generate converting move constructor for covariant generics
        self._gen_covariant_converting_ctor(out, record)

        # Synthesize __iter__() -> self for iterator types (has __next__ but no explicit __iter__)
        has_next = any(m.name == "__next__" for m in record.methods)
        has_iter = any(m.name == "__iter__" for m in record.methods)
        if has_next and not has_iter:
            out.write(f"\n{INDENT}auto& __iter__() {{ return *this; }}\n")

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
        has_begin = self._has_method_or_field(record, "begin")
        has_end = self._has_method_or_field(record, "end")
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

        # Methods qualifying for out-of-line emission go through ``mode="decl"``
        # here; their bodies land in `gen_record_method_defs` at namespace scope.
        dynamic_overrides = self.functions._get_dynamic_override_info(record.name)
        # Track names already dispatched via @overload so the const clone of a
        # @auto_readonly @overload impl is not emitted as a plain method (the
        # mutable clone emits specialized methods for all stubs including const).
        overload_dispatched: set[str] = set()
        for method in record.methods:
            if self._skip_method_emission(method):
                continue
            # Generator methods: emit inline (simple) or declaration-only (complex)
            if method.is_generator:
                if self.gen_generators.is_simple_generator(method):
                    self.gen_generators.gen_simple_generator_inline(
                        out, method, record_name=record.name)
                else:
                    # Declaration only -- body defined after generator struct.
                    # Use the resumable emitter's templated struct name so a
                    # method on a generic class spells the return type as
                    # `__gen_Box_items<T>`, not the bare name.
                    with self.gen_async._resumable_shape(ResumableShape.GENERATOR):
                        struct_name = self.gen_async._struct_name_templated(
                            method, record.name)
                    params = self.gen_async._emit_method_params_decl(
                        method, record.name)
                    const_suffix = " const" if method.is_readonly else ""
                    out.write("\n")
                    # In-class method declaration: do NOT pass record_name --
                    # the enclosing class template's `[T1, ...]` are already
                    # in scope here, so emit only the method's own type
                    # params / proto-typed-param template args (or nothing).
                    self.gen_async._emit_template_header(out, method, indent=INDENT)
                    out.write(f"{INDENT}{struct_name} {method.name}({params}){const_suffix};\n")
                continue
            # Async methods: declaration-only inside the struct; the factory
            # body and the coro struct land in `generator.py`'s post-struct
            # emit pass, parallel to generator methods above.
            if method.is_async:
                struct_name = self.gen_async._struct_name_templated(method, record.name)
                params = self.gen_async._emit_method_params_decl(
                    method, record.name)
                const_suffix = " const" if method.is_readonly else ""
                out.write("\n")
                # In-class method declaration: do NOT pass record_name --
                # the enclosing class template's `[T1, ...]` are already in
                # scope here, so emit only the method's own type params /
                # proto-typed-param template args (or nothing).
                self.gen_async._emit_template_header(out, method, indent=INDENT)
                out.write(f"{INDENT}{struct_name} {method.name}({params}){const_suffix};\n")
                continue
            # @overload-dispatched methods stay inline-in-struct regardless of
            # body size -- the specialized-method emitters take a `mode` param
            # but it's always "inline" here (small/large split for those would
            # need partition-aware overload dispatch; not worth the wiring yet).
            overload_stubs = self.ctx.analyzer.overload_groups.get(id(method))
            if overload_stubs:
                overload_dispatched.add(method.name)
                if self.functions._overload_stubs_are_literal_only(overload_stubs, method):
                    for stub in overload_stubs:
                        self.functions._gen_literal_specialized_method(
                            out, method, stub, record.name,
                            record_type_param_bounds=record.type_param_bounds or None,
                            dynamic_overrides=dynamic_overrides,
                        )
                else:
                    for stub in overload_stubs:
                        self.functions._gen_overload_specialized_method(
                            out, method, stub, record.name,
                            record_type_param_bounds=record.type_param_bounds or None,
                            dynamic_overrides=dynamic_overrides,
                        )
            elif method.name in overload_dispatched:
                # Const clone of a @auto_readonly @overload impl -- already emitted above.
                pass
            else:
                method_mode = "decl" if self._method_can_be_out_of_line(method, record) else "inline"
                self.functions.gen_method_def(out, method, record.name, dynamic_overrides,
                                                record_type_param_bounds=record.type_param_bounds or None,
                                                mode=method_mode)

        # Generate const operator[] for subscript read syntax (obj[i])
        self._gen_subscript_operators(out, record)

        # Generate binary operators from dunder methods (enables protocol conformance)
        self._gen_binary_operators(out, record)

        # Generate operator*() for types with __deref__ (C++ interop)
        self._gen_deref_operators(out, record)

        # Generate size() from __len__ for STL compatibility
        self._gen_size_method(out, record)

        # Generate operator() from __call__ (callable objects)
        self._gen_call_operator(out, record)

        # Generate unary operators from dunder methods
        self._gen_unary_operators(out, record)

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

        has_str_repr = self._record_has_str_repr(record_info)
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

    def _gen_nested_ostream_operators(self, out: TextIO, record: TpyRecord) -> None:
        """Emit operator<< for all nested records (must be at namespace scope)."""
        for nested_rec in record.nested_records:
            if self._is_native(nested_rec):
                continue
            self._gen_record_ostream(out, nested_rec)
            self._gen_nested_ostream_operators(out, nested_rec)

    @staticmethod
    def _skip_method_emission(method: TpyFunction) -> bool:
        """True for methods that don't get C++ emitted as a struct member.

        Shared by ``gen_record_decl`` and ``gen_record_method_defs`` so the
        in-class decl pass and the out-of-line def pass agree on what is
        a method (drift here would link-error or double-emit).

        - ``__init__`` / ``__del__``: handled by dedicated code paths
          (constructor + destructor).
        - Bodyless ``@overload`` stubs: the trailing impl emits all
          overloads. Bodied ``@overload`` stubs (mode b) self-emit and so
          aren't filtered here.
        - ``skip_codegen``: ``@inline`` methods get inlined at call sites.
        """
        if method.name in ("__init__", "__del__"):
            return True
        if method.is_overload_stub and method.is_stub:
            return True
        if method.skip_codegen:
            return True
        return False

    def _method_can_be_out_of_line(self, method: TpyFunction, record: TpyRecord) -> bool:
        """Return True iff this method should have its body emitted at namespace
        scope (after every struct decl) instead of inline inside the struct.

        Out-of-line emission is the default for plain non-template methods so
        that body-references between records don't impose ordering constraints
        on struct decls. Anything that produces a C++ template header has to
        stay inline because the body must be visible at every instantiation.
        Dynamic protocol params lower to ``Base&`` (no template header), so
        they're fine to move out-of-line.
        """
        if record.type_params:
            return False
        if method.type_params:
            return False
        if self.functions.protocols.get_all_protocol_params(method.params):
            return False
        if self.functions._collect_fn_params(method.params):
            return False
        return True

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

    def gen_record_method_defs(self, out: TextIO, record: TpyRecord,
                                *, mode: MethodEmitMode) -> None:
        """Emit out-of-line method definitions for ``record`` at namespace
        scope. ``mode`` selects the partition: ``"def_hpp"`` emits the small
        bodies as ``inline`` in the header, ``"def_cpp"`` emits the larger
        bodies without ``inline`` in the source. The same skip set
        (``__init__``/``__del__``, generators, overload-dispatched,
        templated) applies to both.
        """
        if self._is_native(record):
            return
        # Recurse into nested records first so a nested method's def sits next
        # to its outer container's defs in the file (matches struct emission
        # order).
        for nested in record.nested_records:
            self.gen_record_method_defs(out, nested, mode=mode)
        if record.type_params:
            # Templated class: methods stay inline-in-struct; nothing to emit
            # here. (`_method_can_be_out_of_line` already returned False.)
            return
        dynamic_overrides = self.functions._get_dynamic_override_info(record.name)
        overload_dispatched: set[str] = set()
        want_small = mode == "def_hpp"
        for method in record.methods:
            if self._skip_method_emission(method):
                continue
            # Generators / async methods self-emit through GeneratorCodegen /
            # AsyncCoroCodegen elsewhere (their factory bodies are inline
            # methods that return the coro/generator struct, emitted next to
            # the struct definition).
            if method.is_generator or method.is_async:
                continue
            # Overload-dispatched methods emit specialized methods inline (one
            # per stub) inside the struct -- not handled here. Track them so
            # the const clone of an `@auto_readonly @overload` impl, which
            # immediately follows the mutable impl in `record.methods`, also
            # gets skipped (the mutable clone already emitted both stubs).
            overload_stubs = self.ctx.analyzer.overload_groups.get(id(method))
            if overload_stubs:
                overload_dispatched.add(method.name)
                continue
            if method.name in overload_dispatched:
                continue
            if not self._method_can_be_out_of_line(method, record):
                continue
            # Cycle members (Phase 8) skip the def_hpp partition entirely
            # (the .hpp can only see <peer>_fwd.hpp; inline bodies that
            # touch a peer's complete type would fail to compile). All
            # out-of-line bodies for cycle members go to .cpp regardless
            # of size.
            if not self.ctx.cycle_peers:
                if self._method_body_is_small(method) != want_small:
                    continue
            elif mode == "def_hpp":
                continue
            self.functions.gen_method_def(
                out, method, record.name, dynamic_overrides,
                record_type_param_bounds=record.type_param_bounds or None,
                mode=mode,
            )

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
        """Return (has_str, has_repr) for a record, walking non-native ancestors.

        Operator<< prefers __str__ then __repr__; if neither is found in the
        chain we fall back to the field-by-field default. Native ancestors
        are skipped: BaseException.__str__ would otherwise override the
        default for user Exception subclasses.
        """
        if record_info is None:
            return False, False
        has_str = bool(record_info.get_method_overloads("__str__"))
        has_repr = bool(record_info.get_method_overloads("__repr__"))
        if has_str and has_repr:
            return True, True
        for anc in self.ctx.analyzer.registry.iter_ancestor_records(record_info):
            if anc.is_native:
                continue
            if not has_str and anc.get_method_overloads("__str__"):
                has_str = True
            if not has_repr and anc.get_method_overloads("__repr__"):
                has_repr = True
            if has_str and has_repr:
                break
        return has_str, has_repr

    def _gen_record_ostream(
        self, out: TextIO, record: TpyRecord,
        has_str_repr: tuple[bool, bool] | None = None,
    ) -> None:
        """Generate operator<< overload for printing a record. ``has_str_repr``
        may be passed by callers that already computed the (has_str, has_repr)
        pair to avoid a second ancestor walk."""
        # Use :: for nested types (e.g., Outer::Inner)
        name = escape_cpp_name(record.name.replace(".", "::"))

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
            type_args = ", ".join(record.type_params)
            out.write(f"\ntemplate<{params}>\n")
            out.write(f"inline std::ostream& operator<<(std::ostream& os, const {name}<{type_args}>& obj) {{\n")
        else:
            out.write(f"\ninline std::ostream& operator<<(std::ostream& os, const {name}& obj) {{\n")

        if has_str:
            out.write(f"{INDENT}os << obj.__str__();\n")
        elif has_repr:
            out.write(f"{INDENT}os << obj.__repr__();\n")
        else:
            out.write(f'{INDENT}::tpy::print_object_default(os, "{record.name}", obj);\n')

        out.write(f"{INDENT}return os;\n")
        out.write("}\n")

    def _gen_move_and_destructor(self, out: TextIO, record: TpyRecord) -> None:
        """Generate a C++ destructor with drop-flag protection from a __del__ method.

        Emits:
        1. Custom move constructor that sets source's __tpy_owned_ = false
        2. Custom move assignment via destroy-and-reconstruct (runs destructor on
           old value, then placement-new move-constructs the new value)
        3. Destructor guarded by __tpy_owned_ to skip body on moved-from objects

        super().__del__() calls are dropped -- parent destructors are called
        automatically by C++ after the child destructor body runs.
        """
        del_method = record.del_method
        if del_method is None:
            return

        name = record.name
        cpp_name = escape_cpp_name(name)
        record_info = self.ctx.analyzer.registry.get_record(name)

        # Filter out super().__del__() calls -- they're automatic in C++
        body_stmts = [s for s in del_method.body if not is_super_del_call(s)]

        # --- Custom move constructor ---
        init_parts = []
        if record_info:
            for p in record_info.parents:
                init_parts.append(f"{p.to_cpp()}(std::move(other))")
        for fld in record.fields:
            cpp_fld = escape_cpp_name(fld.name)
            init_parts.append(f"{cpp_fld}(std::move(other.{cpp_fld}))")

        init_list = ""
        if init_parts:
            init_list = " : " + ", ".join(init_parts)

        out.write(f"{INDENT}{cpp_name}({cpp_name}&& other) noexcept{init_list} {{\n")
        out.write(f"{INDENT}{INDENT}other.__tpy_owned_ = false;\n")
        out.write(f"{INDENT}}}\n")

        # --- Custom move assignment (destroy-and-reconstruct) ---
        out.write(f"{INDENT}{cpp_name}& operator=({cpp_name}&& other) noexcept {{\n")
        out.write(f"{INDENT}{INDENT}if (this != &other) {{\n")
        out.write(f"{INDENT}{INDENT}{INDENT}this->~{cpp_name}();\n")
        out.write(f"{INDENT}{INDENT}{INDENT}new (this) {cpp_name}(std::move(other));\n")
        out.write(f"{INDENT}{INDENT}}}\n")
        out.write(f"{INDENT}{INDENT}return *this;\n")
        out.write(f"{INDENT}}}\n")

        # --- Destructor with drop-flag guard ---
        self.ctx.emit_preceding_comments(out, del_method.loc, indent=INDENT)
        self.ctx.emit_source_comment(out, del_method.loc, indent=INDENT)
        out.write(f"\n{INDENT}~{cpp_name}() {{\n")
        out.write(f"{INDENT}{INDENT}if (!this->__tpy_owned_) return;\n")
        if body_stmts:
            local_ns = Namespace(parent=self.ctx.analyzer.global_ns)
            local_ns.bind_variable("self", NominalType(name))
            self.functions.gen_body(out, body_stmts, [], del_method.return_type,
                                    del_method, local_ns, indent_level=2, is_method=True,
                                    record_type_param_bounds=record.type_param_bounds or None,
                                    owning_record_name=name)
        out.write(f"{INDENT}}}\n")

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
            parent_cpp = self.types.type_to_cpp(p)
            init_parts.append(f"{parent_cpp}(std::move(__other))")
        for fld in record.fields:
            cpp_fld = escape_cpp_name(fld.name)
            init_parts.append(f"{cpp_fld}(std::move(__other.{cpp_fld}))")
        init_list = ""
        if init_parts:
            init_list = " : " + ", ".join(init_parts)

        out.write(f"\n{INDENT}template<{template_str}>\n")
        out.write(f"{INDENT}{INDENT}requires ({requires_str})\n")
        out.write(f"{INDENT}{cpp_name}({cpp_name}<{other_type_args}>&& __other) noexcept{init_list} {{\n")
        if record_info.has_del:
            out.write(f"{INDENT}{INDENT}__other.__tpy_owned_ = false;\n")
        out.write(f"{INDENT}}}\n")

        # Friend declaration for cross-instantiation member access
        friend_tparams = ", ".join("typename" for _ in record_info.type_params)
        out.write(f"{INDENT}template<{friend_tparams}> friend struct {cpp_name};\n")

    def _build_init_local_ns(self, record: TpyRecord) -> Namespace:
        """Constructor-body namespace: `self` + every __init__ param.

        Used both for MIL hoist (so binding-based dispatch resolves the same
        way it does for the body) and as gen_body's starting namespace for
        the non-init body.
        """
        local_ns = Namespace(parent=self.ctx.analyzer.global_ns)
        local_ns.bind_variable("self", NominalType(record.name))
        for pname, ptype in record.init_method.params:
            local_ns.bind_variable(pname, ptype)
        return local_ns

    def _extract_base_inits(self, init_method: TpyFunction, record: TpyRecord) -> list[str]:
        """Extract base-init calls (super().__init__() and BaseN.__init__(self, ...))
        and return C++ base initializer strings in declaration order.

        Returns zero or one entry for single-base classes (the super() call, if
        present), and one entry per base with an explicit init call for
        multi-base classes. C++ runs base constructors in declaration order
        regardless of how the initializer list is written; emitting in that
        order avoids -Wreorder when the user writes BaseN.__init__(self, ...)
        calls in non-declaration order in the child __init__ body.
        """
        record_info = self.ctx.analyzer.registry.get_record(record.name)
        parent_order: dict[int, int] = {}
        if record_info is not None:
            for idx, parent in enumerate(record_info.parents):
                p_info = self.ctx.analyzer.registry.get_record_for_type(parent)
                if p_info is not None:
                    parent_order[id(p_info)] = idx

        entries: list[tuple[int, str]] = []
        for src_idx, stmt in enumerate(init_method.body):
            if not is_base_init_call(stmt):
                continue
            if not isinstance(stmt, TpyExprStmt):
                raise CodeGenError("Expected expression statement for base __init__ call", stmt.loc)
            expr = stmt.expr
            if not isinstance(expr, TpyMethodCall):
                raise CodeGenError("Expected method call for base __init__", stmt.loc)
            parent_type = expr.super_parent_type or expr.unbound_self_parent_type
            if parent_type is None:
                raise CodeGenError("base __init__ call without resolved parent type", stmt.loc)
            args = ", ".join(self.expressions.gen_expr(a) for a in expr.args)
            code = f"{parent_type.to_cpp()}({args})"
            # Unknown-parent entries sort after all known ones (preserves source
            # order between them via src_idx). Shouldn't happen for well-formed
            # programs, but avoids hiding codegen bugs behind a silent drop.
            p_info = self.ctx.analyzer.registry.get_record_for_type(parent_type)
            rank = parent_order.get(id(p_info), len(parent_order) + src_idx) if p_info else len(parent_order) + src_idx
            entries.append((rank, code))
        entries.sort(key=lambda e: e[0])
        return [code for _, code in entries]

    def _extract_field_inits(
        self, init_method: TpyFunction, record: TpyRecord
    ) -> tuple[list[tuple[str, str]], set[int]]:
        """Extract field initializations from __init__ body.

        Only extracts initializations for fields that belong to this class directly,
        not inherited fields. Inherited field assignments must go in the constructor body.

        Returns ``(inits, hoisted_ids)`` -- ``inits`` is the (field, value) pairs to
        emit in the member init list; ``hoisted_ids`` is the set of ``id(stmt)`` for
        TpyAssign statements that were hoisted, used by ``_get_non_init_stmts`` to
        avoid emitting the same assignment again in the constructor body.
        """
        # Build field name -> type map for target type passing
        field_types = {fld.name: fld.type for fld in record.fields}
        own_field_names = set(field_types.keys())
        param_names = {p[0] for p in init_method.params}
        # Temporarily populate movable_locals with constructor Own params so that
        # gen_call_arg/_maybe_move can emit std::move() for last-use args inside
        # init list expressions (e.g. Box.from_optional(next)).
        saved_movable = self.ctx.movable_locals.copy()
        for pname, ptype in init_method.params:
            actual = unwrap_readonly(ptype)
            own = unwrap_optional_own(actual)
            if own is not None and not own.wrapped.is_value_type():
                self.ctx.movable_locals.add(pname)
        # Collect nested def names -- these are lambdas defined in the body,
        # so field inits referencing them must go in the body, not the init list.
        nested_def_names = {
            s.func.name for s in init_method.body if isinstance(s, TpyNestedDef)
        }
        # Body-locals: any field-init whose RHS references one of these must
        # stay in the body (the MIL runs before the constructor body, so the
        # local isn't in scope there). Captures e.g. `self._x = OwnedBar(tmp)`
        # which would otherwise hoist into `Foo() : _x(OwnedBar(tmp)) { auto
        # tmp = ...; }` and fail C++.
        local_names = collect_top_level_local_names(init_method.body)
        try:
            inits = []
            hoisted_ids: set[int] = set()
            # MIL runs as a block BEFORE the constructor body, so hoisting a
            # `self.f = expr` past a side-effecting body statement reorders
            # the expr's evaluation. Hoist the leading chain of `self.f = expr`
            # assignments (skipping super().__init__, docstrings, pass); any
            # non-`self.f = expr` body statement breaks the chain hard.
            #
            # Inherited-field assigns (`self.base_field = expr`) are a special
            # case: they always go to the body (the base ctor owns the MIL
            # slot) but they only mutate `self.base_field`, nothing else. So
            # the chain stays alive past them, with the constraint that any
            # subsequent own-field MIL hoist whose RHS reads `self.base_field`
            # (or calls a method on self, which could read anything) must
            # demote to keep source order.
            chain_broken = False
            body_written_self_fields: set[str] = set()

            def demote(field_name: str, loc: SourceLocation | None, reason: str) -> None:
                nonlocal chain_broken
                chain_broken = True
                self._reject_nondef_ctor_field_in_body(
                    field_name, own_field_names, field_types, loc, reason)

            for stmt in init_method.body:
                if (is_base_init_call(stmt) or is_docstring(stmt)
                        or isinstance(stmt, TpyPassStmt)):
                    continue
                if not (isinstance(stmt, TpyAssign)
                        and isinstance(stmt.target, TpyFieldAccess)
                        and isinstance(stmt.target.obj, TpyName)
                        and stmt.target.obj.name == "self"):
                    chain_broken = True
                    continue
                field_name = stmt.target.field
                source_expr = stmt.value
                while isinstance(source_expr, TpyCoerce):
                    source_expr = source_expr.expr
                if chain_broken:
                    demote(field_name, stmt.loc,
                           "a prior statement in the constructor body would "
                           "run before this initializer")
                    continue
                if isinstance(source_expr, TpyName) and source_expr.name in nested_def_names:
                    demote(field_name, stmt.loc,
                           "the assigned value is a function defined in the body")
                    continue
                # Bare-name RHS not in params, or any reference to a body-local:
                # the value isn't in scope at MIL time.
                blocked_by_bare_name = (
                    isinstance(source_expr, TpyName)
                    and source_expr.name not in param_names
                )
                blocked_by_body_local = bool(
                    local_names and (collect_name_refs(stmt.value) & local_names))
                if blocked_by_bare_name or blocked_by_body_local:
                    demote(field_name, stmt.loc,
                           "the assigned expression references a local defined "
                           "earlier in the body")
                    continue
                # Inherited field: the base ctor owns the MIL slot, so we
                # write through the body but the chain stays alive.
                if field_name not in own_field_names:
                    body_written_self_fields.add(field_name)
                    continue
                if expr_reads_self_field(stmt.value, body_written_self_fields):
                    demote(field_name, stmt.loc,
                           "the initializer reads a `self.<field>` written by an "
                           "earlier inherited-field assignment in the body")
                    continue
                fld_type = field_types[field_name]
                # Unwrap copy() in member init -- init list copies implicitly
                source = self.ctx.unwrap_copy(stmt.value)
                # A copy() of a pointer-repr tuple goes through _gen_copy_expr
                # (storage form, element-wise value copy) rather than the
                # unwrapped borrow-form literal, which mismatches a const source.
                # The result is already storage form, so skip the wrap below.
                copy_storage_tuple = (source is not stmt.value
                                      and isinstance(fld_type, TupleType)
                                      and fld_type.has_pointer_repr_element())
                # A member-initializer-list expression has no place to declare
                # temps; if `gen_expr` registers any, demote the assignment to
                # the body where the next `temps.flush` can emit them.
                checkpoint = self.ctx.temps.checkpoint()
                value = self.expressions.gen_expr(
                    stmt.value if copy_storage_tuple else source, fld_type)
                if self.ctx.temps.rollback_to(checkpoint):
                    demote(field_name, stmt.loc,
                           "the initializer expression requires a codegen "
                           "temporary that cannot be declared in the member "
                           "initializer list (e.g. a varargs call). Refactor "
                           "the RHS so it does not need an intermediate")
                    continue
                # A bytes-view source (e.g. a bytes param's span) into an owned
                # bytes field copies via the shared view->owned chokepoint --
                # vector has no span constructor. str needs no arm (std::string's
                # explicit string_view ctor fires in the direct-init member
                # list). Pass the (coerce-wrapped) stmt.value: a source sema
                # already coerced to bytes resolves as non-view here, so
                # gen_expr's own copy is left unchanged, not double-wrapped.
                if is_bytes_type(fld_type):
                    value = self.expressions._view_source_to_owned(
                        stmt.value, fld_type, value)
                # Auto-move Own[T] (and Own[T] | None, a std::optional<T> by
                # value) params at last use in the member init list. The source
                # is gated to a PARAM (the loop below), so last-use is sufficient
                # without the movable_locals / alias-suppression guard that
                # _is_last_use_movable applies to locals: an Own param is storage
                # form, so a `q = p` aliasing attempt copies rather than aliasing,
                # leaving no live borrow that the move could dangle.
                inner = source
                while isinstance(inner, TpyCoerce):
                    inner = inner.expr
                if (isinstance(inner, TpyName)
                        and id(inner) in self.ctx.analyzer.ctx.all_last_uses):
                    for pname, ptype in init_method.params:
                        if pname == inner.name and unwrap_optional_own(unwrap_readonly(ptype)) is not None:
                            value = f"std::move({value})"
                            break
                # T* sources need conversion to std::optional<T>; field access (std::optional<T>) doesn't.
                # OwnType(OptionalType) params are std::optional<T>&& -- already optional, no conversion.
                if isinstance(fld_type, OptionalType) and fld_type.uses_pointer_repr():
                    # Check if the source param is Own[Optional[T]] -- then the C++ param
                    # is std::optional<T>&& and no ptr_to_optional is needed.
                    source_is_own_optional = False
                    if isinstance(source, TpyName):
                        for pname, ptype in init_method.params:
                            if pname == source.name and unwrap_optional_own(ptype) is not None:
                                source_is_own_optional = True
                                break
                    if not source_is_own_optional and not isinstance(source, TpyFieldAccess):
                        raw_val_type = self.ctx.get_expr_type(stmt.value)
                        val_type = raw_val_type.wrapped if isinstance(raw_val_type, OwnType) else raw_val_type
                        if isinstance(val_type, OptionalType):
                            if not (isinstance(val_type.inner, OwnType)):
                                value = f"::tpy::ptr_to_optional({value})"
                # Tuple field stores std::optional<T>; the param is T*.
                # Lift element-wise. Storage-form sources (field, subscript,
                # global, storage-form local) already match the field shape
                # and skip the wrap.
                if (isinstance(fld_type, TupleType)
                        and fld_type.has_pointer_repr_element()
                        and not self.ctx.is_storage_form_source(source)
                        and not copy_storage_tuple):
                    fld_cpp = self.types.type_to_cpp(fld_type)
                    value = f"::tpy::tuple_to_storage<{fld_cpp}>({value})"
                # Pointer-variant param -> value-variant field: deref+copy.
                # The param is variant<T*...> but the field stores variant<T...>.
                # Skip when the source is a bare alternative value (constructor,
                # field access of a value-variant field, etc.) -- the variant
                # constructs from it directly, and to_value_variant would fail
                # template deduction.
                if (self.ctx.is_ptr_variant_union(fld_type)
                        and self.ctx.is_ptr_variant_source(source)):
                    val_cpp = self.types.type_to_cpp(fld_type)
                    value = f"::tpy::to_value_variant<{val_cpp}>({value})"
                inits.append((field_name, value))
                hoisted_ids.add(id(stmt))
            return inits, hoisted_ids
        finally:
            self.ctx.movable_locals = saved_movable

    def _reject_nondef_ctor_field_in_body(
        self, field_name: str, own_field_names: set[str],
        field_types: dict[str, TpyType], loc: SourceLocation | None,
        reason: str,
    ) -> None:
        """Raise a CodeGenError when a `self.field = expr` that codegen has
        demoted to the constructor body targets an own field whose type has
        a suppressed default ctor (`@nocopy` with `__del__`, etc.). Such a
        field has no default state, so the C++ MIL would implicitly
        default-init it to an uncompilable state. The diagnostic guides the
        user to refactor toward a leading MIL chain (or a @staticmethod
        factory) before the C++ compiler emits something cryptic.
        """
        if field_name not in own_field_names:
            return
        fld_type = field_types[field_name]
        fld_rec = self.ctx.analyzer.registry.get_record_for_type(fld_type)
        if fld_rec is None or not del_suppresses_default_ctor(fld_rec):
            return
        raise CodeGenError(
            f"field '{field_name}' of non-default-constructible type "
            f"'{fld_rec.name}' must be initialized before any local "
            f"variable is bound or any other statement runs in this "
            f"constructor: {reason}. The field has no default constructor, "
            f"so the initializer cannot run later than the member "
            f"initializer list.\n"
            f"  Constructor order: super().__init__() -> field assignments "
            f"(self.x = ...) -> other logic.\n"
            f"  To pre-compute arguments, move the logic into a "
            f"@staticmethod on '{fld_rec.name}'. Two shapes work: "
            f"(a) factory returning Own[Self] -- "
            f"`self.{field_name} = {fld_rec.name}.factory(ctor_params)`; "
            f"(b) helper returning a raw Ptr[T] called from "
            f"'{fld_rec.name}.__init__' -- takes high-level args and "
            f"assigns via `self.{field_name} = {fld_rec.name}(high_level_args)`",
            loc,
        )

    def _render_enum_member_default(self, expr) -> str | None:
        """C++ for a validated enum-member field default
        (`Color.RED` -> `tpyapp::mod::Color::RED`), or None when the
        expr isn't that shape (registration would have rejected it)."""
        if not (isinstance(expr, TpyFieldAccess)
                and isinstance(expr.obj, TpyName)):
            return None
        enum_type = self.ctx.analyzer.registry.get_enum(expr.obj.name)
        if enum_type is None:
            return None
        einfo = enum_info_of(enum_type)
        member_cpp = (einfo.cpp_member_name_map.get(expr.field, expr.field)
                      if einfo is not None else expr.field)
        cur_module = self.ctx.analyzer.ctx.module_name
        return (f"{enum_cpp_name(enum_type, cur_module, einfo=einfo)}"
                f"::{member_cpp}")

    def _all_fields_default_constructible(self, record: TpyRecord) -> bool:
        """Check if all own fields and the parent (if any) are C++-default-constructible.

        Used to decide whether to emit ClassName() = default;. If any field
        or the parent class lacks a C++ default constructor, = default would
        fail to compile.

        For generic records (template classes) we always emit = default: C++ will
        implicitly delete it at the instantiation point if a type arg is not
        default-constructible, which is the correct behaviour.

        Uses C++-level constructibility: a user record is C++-default-constructible
        if it has no __init__ (aggregate) or if all its fields are themselves
        C++-default-constructible (so it will also emit = default).
        """
        # Template classes: C++ handles the constraint at instantiation time.
        if record.type_params:
            return True
        if not all(self._fld_type_cpp_default_constructible(fld.type)
                   for fld in record.fields if fld.default_expr is None):
            return False
        record_info = self.ctx.analyzer.ctx.registry.get_record(record.name)
        if record_info is not None:
            for p in record_info.parents:
                if not self._fld_type_cpp_default_constructible(p):
                    return False
        return True

    def _fld_type_cpp_default_constructible(self, typ: TpyType) -> bool:
        """Check if a TPy type maps to a C++-default-constructible type.

        For non-record types and generic instantiations delegates to the
        sema-level _is_default_constructible (primitives, containers, Optional,
        and records-with-all-default-params all return True there).

        For non-generic user records, checks C++-level constructibility
        recursively: a record with __init__ is C++-default-constructible if
        all its own fields are too (meaning it will also emit = default).
        """
        protocols = self.ctx.analyzer.protocols
        # Tuple/Array: must check element types with C++-level logic because
        # std::tuple<T>/std::array<T,N> are default-constructible iff T is.
        if isinstance(typ, TupleType):
            return all(self._fld_type_cpp_default_constructible(et) for et in typ.element_types)
        if is_array(typ):
            elem = typ.get_element_type()
            return elem is not None and self._fld_type_cpp_default_constructible(elem)
        # Enum types map to C++ enum class, which is trivially constructible.
        if is_enum_type(typ):
            return True
        # Raw pointers are trivially constructible (just uninitialized).
        if isinstance(typ, PtrType):
            return True
        if not isinstance(typ, NominalType) or not typ.is_user_record:
            return protocols._is_default_constructible(typ)
        # Generic instantiation (e.g. Pair[Int32]): the base template class
        # usually emits = default, so any instantiation is C++-default-constructible.
        # `del_suppresses_default_ctor` catches the exception (Box/Rc/Weak and
        # other __del__ shapes) so the cascade through enclosing records stops
        # at the first non-default-constructible field with a clean diagnostic
        # rather than a confusing implicit-delete chain (Outer -> Holder -> Rc<T>).
        if typ.type_args:
            base_rec = self.ctx.analyzer.ctx.registry.get_record(typ.name)
            if base_rec is not None and base_rec.type_params:
                if del_suppresses_default_ctor(base_rec):
                    return False
                return True  # generic class emits = default
            return protocols._is_default_constructible(typ)
        record_info = self.ctx.analyzer.ctx.registry.get_record(typ.name)
        if record_info is None:
            return False
        if del_suppresses_default_ctor(record_info):
            return False
        if not record_info.has_init:
            return True  # aggregate: always C++ default-constructible
        # Non-generic record with __init__: default-constructible iff all parents and own fields are
        for p in record_info.parents:
            if not self._fld_type_cpp_default_constructible(p):
                return False
        return all(self._fld_type_cpp_default_constructible(f.type) for f in record_info.fields)

    def _get_non_init_stmts(
        self, init_method: TpyFunction, record: TpyRecord, hoisted_ids: set[int]
    ) -> list[TpyStmt]:
        """Get statements from __init__ that aren't hoisted into the member init list.

        These need to go in the constructor body. ``hoisted_ids`` is produced by
        ``_extract_field_inits`` and contains ``id(stmt)`` for every TpyAssign that
        was successfully placed in the MIL -- the single source of truth so the
        two functions can never disagree on which assignments belong where.
        Skips ``super().__init__()`` calls (handled separately as base initializer).
        """
        non_init = []
        for stmt in init_method.body:
            if is_base_init_call(stmt):
                continue
            if id(stmt) in hoisted_ids:
                continue
            non_init.append(stmt)
        return non_init

    def _gen_subscript_operators(self, out: TextIO, record: TpyRecord) -> None:
        """Generate operator[] for subscript read syntax.

        For readonly __getitem__: dual overloads (const + non-const) matching
        the method itself. Writes go through ::tpy::__setitem__.
        For non-readonly __getitem__: non-const only (method mutates self).
        For @overload __getitem__: generate operator[] for each stub.
        For @auto_readonly __getitem__ clone pairs: const first, then mutable,
        one per clone (no duplicate).
        """
        # Gather all non-stub __getitem__ implementations.
        # @auto_readonly cloning produces two: one mutable, one const.
        getitem_impls = [
            m for m in record.methods
            if m.name == "__getitem__" and not m.is_overload_stub
        ]

        if not getitem_impls:
            return

        # Check if this __getitem__ has @overload stubs (use first impl for lookup)
        overload_stubs = self.ctx.analyzer.overload_groups.get(id(getitem_impls[0]))
        if overload_stubs:
            self._gen_overload_subscript_operators(out, overload_stubs)
            return
        # auto_readonly clone pair: const operator first, then mutable.
        pair = _split_readonly_clone_pair(getitem_impls)
        if pair is not None:
            const_impl, mutable_impl = pair
            self._gen_const_subscript_operator(out, const_impl)
            self._gen_mutable_subscript_operator(out, mutable_impl)
            return
        self._gen_single_subscript_operator(out, getitem_impls[0])

    def _gen_overload_subscript_operators(self, out: TextIO, stubs: list) -> None:
        """Generate operator[] for @overload __getitem__, handling auto_readonly clone pairs.

        Stubs may include mutable+const clone pairs (from @overload @auto_readonly).
        For each unique parameter type: if both mutable and const clones exist, generate
        const first then mutable; otherwise delegate to _gen_single_subscript_operator.
        """
        # Group stubs by their first parameter type to detect clone pairs.
        # Use param type string as key since TpyType equality works correctly.
        by_param: dict[str, list] = defaultdict(list)
        for stub in stubs:
            if stub.params:
                key = str(stub.params[0][1])
                by_param[key].append(stub)
            else:
                self._gen_single_subscript_operator(out, stub)

        for param_type_str, group in by_param.items():
            pair = _split_readonly_clone_pair(group)
            if pair is not None:
                const_stub, mutable_stub = pair
                # Clone pair: const operator first, then mutable.
                self._gen_const_subscript_operator(out, const_stub)
                # Mutable only if return could be a reference.
                needs_dual = (not mutable_stub.return_type.is_value_type()
                              or isinstance(mutable_stub.return_type, TypeParamRef))
                if needs_dual:
                    self._gen_mutable_subscript_operator(out, mutable_stub)
                continue
            # No clone pair: emit each stub via the standard logic.
            for stub in group:
                self._gen_single_subscript_operator(out, stub)

    def _gen_single_subscript_operator(self, out: TextIO, method: 'TpyFunction') -> None:
        """Generate a single operator[] overload delegating to __getitem__."""
        if not method.params:
            return
        if method.is_readonly:
            self._gen_const_subscript_operator(out, method)
            # Non-const overload only when return could be a reference
            needs_dual = not method.return_type.is_value_type() or isinstance(method.return_type, TypeParamRef)
            if needs_dual:
                self._gen_mutable_subscript_operator(out, method)
        else:
            self._gen_mutable_subscript_operator(out, method)

    def _gen_const_subscript_operator(self, out: TextIO, method: 'TpyFunction') -> None:
        """Generate a const operator[] overload (read-only subscript)."""
        if not method.params:
            return
        index_param_name, index_type = method.params[0]
        index_cpp = index_type.to_cpp()
        ret_const = method.return_type.to_cpp_return_const()
        out.write(f"\n{INDENT}{ret_const} operator[]({index_cpp} {index_param_name}) const {{\n")
        out.write(f"{INDENT}{INDENT}return __getitem__({index_param_name});\n")
        out.write(f"{INDENT}}}\n")

    def _gen_mutable_subscript_operator(self, out: TextIO, method: 'TpyFunction') -> None:
        """Generate a mutable (non-const) operator[] overload."""
        if not method.params:
            return
        index_param_name, index_type = method.params[0]
        index_cpp = index_type.to_cpp()
        ret_mut = method.return_type.to_cpp_return()
        out.write(f"\n{INDENT}{ret_mut} operator[]({index_cpp} {index_param_name}) {{\n")
        out.write(f"{INDENT}{INDENT}return __getitem__({index_param_name});\n")
        out.write(f"{INDENT}}}\n")

    def _gen_binary_operators(self, out: TextIO, record: TpyRecord) -> None:
        """Generate C++ operators from dunder methods (arithmetic and comparison).

        This enables user records to conform to C++ concepts that use operator syntax
        (e.g., `t + other`, `t < other`) rather than method calls (e.g., `t.__add__(other)`).
        """
        for method in record.methods:
            if method.name not in DUNDER_TO_BINARY_OP:
                continue
            if not method.params:
                continue  # Binary operators need at least one parameter

            cpp_op = DUNDER_TO_BINARY_OP[method.name]
            param_name, param_type = method.params[0]
            param_cpp = param_type.to_cpp_const_param(param_name)

            # Return type - use to_cpp() for value/Own types
            ret_cpp = method.return_type.to_cpp()

            # Generate friend operator that delegates to the dunder method
            # Using friend function allows symmetric operand handling
            rec_short = bare_name(record.name)
            out.write(f"\n{INDENT}friend {ret_cpp} operator{cpp_op}(const {escape_cpp_name(rec_short)}& lhs, {param_cpp}) {{\n")
            out.write(f"{INDENT}{INDENT}return lhs.{method.name}({param_name});\n")
            out.write(f"{INDENT}}}\n")

    def _gen_call_operator(self, out: TextIO, record: TpyRecord) -> None:
        """Generate operator() delegating to __call__ (callable objects).

        Enables obj(args) syntax and std::invocable concept conformance.
        """
        for method in record.methods:
            if method.name != "__call__":
                continue
            # An @overload group's impl is not emitted as-is (the per-stub
            # specializations carry its body), so it must not get an
            # operator() either -- the stubs' delegations cover every
            # emitted __call__ signature, with matching const-ness.
            if self.ctx.analyzer.overload_groups.get(id(method)):
                continue
            ret_cpp = method.return_type.to_cpp()
            const_suffix = " const" if method.is_readonly else ""
            params_cpp = ", ".join(
                p_type.to_cpp_const_param(p_name)
                for p_name, p_type in method.params
            )
            arg_names = ", ".join(p_name for p_name, _ in method.params)
            out.write(f"\n{INDENT}{ret_cpp} operator()({params_cpp}){const_suffix} {{\n")
            out.write(f"{INDENT}{INDENT}return __call__({arg_names});\n")
            out.write(f"{INDENT}}}\n")

    def _gen_deref_operators(self, out: TextIO, record: TpyRecord) -> None:
        """Generate operator*() for types with __deref__().

        Enables C++ interop: *box instead of box.__deref__().
        Only for user-defined types -- Ptr[T] and Ptr[readonly[T]] map to raw T*
        which already support *ptr natively.

        Mirrors the __getitem__ dual-overload pattern: an @auto_readonly
        __deref__ clone pair (mutable + const) gets both operator*() and
        operator*() const so const receivers can dereference. A single
        __deref__ keeps the single overload.
        """
        deref_impls = [m for m in record.methods if m.name == "__deref__"]
        if not deref_impls:
            return

        if _split_readonly_clone_pair(deref_impls) is not None:
            self._emit_operator_star(out)
            self._emit_operator_star(out, qual=" const")
            return

        self._emit_operator_star(out)

    def _emit_operator_star(self, out: TextIO, qual: str = "") -> None:
        out.write(f"\n{INDENT}auto operator*(){qual} -> decltype(__deref__()) {{\n")
        out.write(f"{INDENT}{INDENT}return __deref__();\n")
        out.write(f"{INDENT}}}\n")

    def _has_method_or_field(self, record: TpyRecord, name: str) -> bool:
        """Check if a record has a method or field with the given name."""
        return (any(m.name == name for m in record.methods)
                or any(f.name == name for f in record.fields))

    def _gen_size_method(self, out: TextIO, record: TpyRecord) -> None:
        """Generate size() from __len__ for STL compatibility.

        Enables user types with __len__ to work with C++ algorithms and
        container concepts that expect a size() method.
        """
        len_method = None
        for m in record.methods:
            if m.name == "__len__":
                len_method = m
                break
        if len_method is None:
            return
        if self._has_method_or_field(record, "size"):
            return
        ret_cpp = len_method.return_type.to_cpp()
        ret_type = len_method.return_type
        ret_int_tr = int_traits_of(ret_type)
        is_signed = is_big_int_type(ret_type) or (ret_int_tr is not None and ret_int_tr.signed)
        if ret_cpp == "size_t":
            out.write(f"\n{INDENT}size_t size() const {{ return __len__(); }}\n")
        elif is_signed:
            out.write(f"\n{INDENT}size_t size() const {{\n")
            out.write(f"{INDENT}{INDENT}auto len = __len__();\n")
            out.write(f"{INDENT}{INDENT}if (len < 0) ::tpy::raise<::tpy::ValueError>(\"__len__() should return >= 0\");\n")
            out.write(f"{INDENT}{INDENT}return static_cast<size_t>(len);\n")
            out.write(f"{INDENT}}}\n")
        else:
            out.write(f"\n{INDENT}size_t size() const {{ return static_cast<size_t>(__len__()); }}\n")

    def _gen_unary_operators(self, out: TextIO, record: TpyRecord) -> None:
        """Generate C++ unary operators from dunder methods."""
        DUNDER_TO_UNARY_OP = {
            "__neg__": "-", "__pos__": "+", "__invert__": "~",
        }
        for method in record.methods:
            if method.name not in DUNDER_TO_UNARY_OP:
                continue
            if method.params:
                continue  # Unary operators take no params
            cpp_op = DUNDER_TO_UNARY_OP[method.name]
            ret_cpp = method.return_type.to_cpp()
            rec_short = bare_name(record.name)
            out.write(f"\n{INDENT}friend {ret_cpp} operator{cpp_op}(const {escape_cpp_name(rec_short)}& operand) {{\n")
            out.write(f"{INDENT}{INDENT}return operand.{method.name}();\n")
            out.write(f"{INDENT}}}\n")
