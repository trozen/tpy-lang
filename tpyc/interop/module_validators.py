"""Whole-program `# tpy: ext_module` / `@export` validation.

Run by the compiler orchestrator after all modules are analyzed (the checks
need cross-module knowledge: locality of exposed types, resolved
registries) and before codegen, so the glue emitter can assume valid
input. The bodies live here beside the shared shape predicates
(`export_shape.py`) and the glue emitter (`extension.py`);
`Compiler._validate_ext_module_exports` stays as the thin phase hook.
"""
from __future__ import annotations
from typing import TYPE_CHECKING

from ..compiler import CompileError
from ..diagnostics import Diagnostic, DiagnosticLevel
from ..typesys import FunctionInfo, TpyType
from .export_shape import (
    export_method_shape_error, exposed_view_field,
    nocopy_borrow_return_error, unsupported_boundary_param_form)

if TYPE_CHECKING:
    from ..compiler import Compiler, CompiledModule
    from ..parse import SourceLocation, TpyFunction, TpyRecord


def validate_ext_module_exports(compiler: 'Compiler') -> None:
    """Validate `# tpy: ext_module` preconditions before codegen, so the
    glue emitter can assume valid input:

      - ext_module + native_module is contradictory: native is
        declaration-only (no generated code, hence no module body to attach
        PyInit_ to), so the combination would emit a PyInit_-less .so. The
        empty-hpp early-return in _generate_code_impl is reachable only via
        this combo, so rejecting it here removes the silent drop.
      - Every @export-ed function's param/return types must have a CPython
        boundary marshaller. Runs post-sema so the types are resolved.
    """
    from ..type_def_registry import (
        is_function_boundary_marshallable, is_span_boundary_param,
        boundary_type_name, boundary_unmarshallable_msg)
    for compiled in compiler.modules.values():
        if not compiled.ast.directives.ext_module:
            continue
        if compiled.ast.directives.native_module:
            loc = compiled.ast.directives.ext_module_loc
            raise CompileError(
                "a module cannot be both `# tpy: ext_module` and "
                "`# tpy: native_module` (native is declaration-only and "
                "emits no CPython module to export)",
                compiled.name, compiled.path,
                lineno=loc.line if loc else None)
        for func in compiled.ast.functions:
            if not func.exposed_to_host:
                continue
            fline = func.loc.line if func.loc else None
            form = unsupported_boundary_param_form(func)
            if form is not None:
                raise CompileError(
                    f"@export function '{func.name}': {form}",
                    compiled.name, compiled.path, lineno=fline)
            # `void` (no annotation or `-> None`) is a legal return -- the
            # wrapper hands back None -- but never a valid parameter type.
            checks = [(func.return_type, "return", "return")]
            checks += [(ptype, f"parameter '{pname}'", "param")
                       for pname, ptype in func.params]
            for typ, what, role in checks:
                form_err = _exposed_form_error(
                    typ, role, compiled.analyzer.registry, compiled)
                if form_err is not None:
                    raise CompileError(
                        f"@export function '{func.name}': {what} {form_err}",
                        compiled.name, compiled.path, lineno=fline)
                # Span[T] numeric crosses only as a param (buffer-protocol
                # copy-in); a function hands back numeric data via list[T]
                # instead, so this is checked here rather than folded into
                # is_function_boundary_marshallable (which is otherwise
                # direction-symmetric for every boundary type).
                if role == "param" and is_span_boundary_param(typ):
                    continue
                if is_function_boundary_marshallable(typ, role == "return"):
                    continue
                raise CompileError(
                    boundary_unmarshallable_msg(
                        func.name, what, boundary_type_name(typ)),
                    compiled.name, compiled.path, lineno=fline)
            _warn_export_copy_boundary_mutation(compiled, func)
        for record in compiled.ast.records:
            if not record.exposed_to_host:
                continue
            _validate_exposed_class(compiled, record)
        _validate_exposed_enums(compiled)
        _validate_user_exc_data_fields(compiled)

def _warn_export_copy_boundary_mutation(compiled: 'CompiledModule',
                                        func: 'TpyFunction') -> None:
    """A container or Span param crosses the @export boundary copy-in (the
    boundary marshals an owned copy), so a structural mutation -- one
    visible through the reference, e.g. append / setitem / element
    assignment -- is silently lost to the Python caller. Warn precisely
    where sema proved that happens; a non-mutating param has no observable
    divergence and stays quiet. tuple is a value type (and immutable), so
    it is exempt; Span[readonly[T]] can never appear here (writing through
    it is already a compile error), so only a mutated Span[T] shows up."""
    fis = compiled.analyzer.registry.functions.get(func.name)
    if not fis:
        return
    fi = next((f for f in fis if len(f.params) == len(func.params)), fis[0])
    _warn_copy_boundary_mutation(
        compiled, f"@export function '{func.name}'", fi,
        list(enumerate(func.params)), func.loc)

def _warn_copy_boundary_mutation(
        compiled: 'CompiledModule', label: str, fi: FunctionInfo,
        params: list[tuple[int, tuple[str, TpyType]]],
        loc: SourceLocation | None) -> None:
    """Shared core of the copy-in mutation warning for free @export
    functions and exposed-class methods. `params` is a list of
    (index, (name, type)) with indices aligned to `fi.mutated_params`
    (so a method's entries keep their self-inclusive positions)."""
    from ..type_def_registry import (
        is_list, is_dict, is_set, is_span, _boundary_inner)
    # `mutated_params` (not `structural_mutated_params`) is the caller-visible
    # write-back fact: it covers element assignment (d[k] = v) that the
    # narrower structural set omits. A reference-type container param is
    # passed by reference, so any mutation here would write through.
    mut = fi.mutated_params or frozenset()
    for i, (pname, ptype) in params:
        if i not in mut:
            continue
        inner = _boundary_inner(ptype)  # strip the auto-inserted Ref/borrow
        kind = ("list" if is_list(inner) else
                "dict" if is_dict(inner) else
                "set" if is_set(inner) else
                "Span" if is_span(inner) else None)
        if kind is None:
            continue
        # The per-module analyzer sink (not the compiler-level one) is what
        # the test harness's `# tpyc: warning(...)` annotation validation
        # reads, matching the other @export diagnostics.
        compiled.analyzer.diagnostics.append(Diagnostic(
            DiagnosticLevel.WARNING,
            f"{label}: {kind} parameter '{pname}' is "
            f"copied in at the CPython boundary; mutations to it are not "
            f"visible to the caller",
            loc))

def _validate_exposed_enums(compiled: 'CompiledModule') -> None:
    """An `@export` enum is recreated as a CPython IntEnum/Enum at PyInit_.
    Reject the forms the glue does not rebuild: a `@native` enum (its
    members/values come from the C++ side, not a TPy-declared value table)
    and a nested enum (only top-level module enums are exposed this rung) --
    rather than silently dropping them.
    """
    for enum in compiled.ast.enums:
        if not enum.exposed_to_host:
            continue
        if enum.is_native:
            loc = enum.loc.line if enum.loc else None
            raise CompileError(
                f"@export enum '{enum.name}': a @native enum cannot be "
                "exposed to CPython (its values come from C++, not a "
                "TPy-declared member table)",
                compiled.name, compiled.path, lineno=loc)
    for record in compiled.ast.records:
        for enum in record.nested_enums:
            if enum.exposed_to_host:
                loc = enum.loc.line if enum.loc else None
                raise CompileError(
                    f"@export enum '{enum.name}': a nested enum cannot be "
                    "exposed to CPython yet (only top-level module enums "
                    "are exposed)",
                    compiled.name, compiled.path, lineno=loc)

def _validate_user_exc_data_fields(compiled: 'CompiledModule') -> None:
    """A data-carrying user exception crosses its instance fields to CPython
    as attributes (PyErr_SetObject over a constructed instance). v1 marshals
    scalar/str/bytes/exposed-enum fields via the getset single-value path; a
    container or exposed-class field has no attribute-marshal emit yet, so
    reject it (located) rather than emit a glue TU that won't compile.
    """
    from ..type_def_registry import (
        is_boundary_marshallable, is_exposed_class, is_exposed_enum,
        is_internal_boundary_field, boundary_type_name)
    reg = compiled.analyzer.registry
    for record in compiled.ast.records:
        info = reg.get_record(record.name)
        if info is None or info.is_native:
            continue
        if not (info.implements_throwable and info.inherits_base_exception):
            continue

        def line_of(fld) -> 'int | None':
            return (fld.loc.line if fld.loc else
                    (record.loc.line if record.loc else None))

        for fld in reg.user_declared_fields(info):
            if is_internal_boundary_field(fld.name):
                continue  # internal payload state; never crosses
            if is_exposed_enum(fld.type):
                # A locally-defined exposed enum crosses; a cross-module one
                # has no type handle in this glue TU. Reuse the sibling
                # locality check so the two paths can't drift.
                form_err = _exposed_form_error(
                    fld.type, "field", reg, compiled)
                if form_err is not None:
                    raise CompileError(
                        f"user exception '{record.name}': data field "
                        f"'{fld.name}' {form_err}",
                        compiled.name, compiled.path, lineno=line_of(fld))
                continue
            # Exposed-class fields are excluded explicitly (even value-type):
            # the attribute path can't marshal a nested class instance yet
            # (deferred). Containers fail the base marshallable check.
            if (is_boundary_marshallable(fld.type, False)
                    and not is_exposed_class(fld.type)):
                continue
            raise CompileError(
                f"user exception '{record.name}': data field '{fld.name}' "
                f"of type {boundary_type_name(fld.type)} cannot cross the "
                f"CPython boundary as an instance attribute (only scalar, "
                f"str, bytes, and exposed-enum exception fields are "
                f"supported yet)",
                compiled.name, compiled.path, lineno=line_of(fld))

def _validate_exposed_class(compiled: 'CompiledModule',
                            record: 'TpyRecord') -> None:
    """An `@export` class is exposed as a flat PyType_FromSpec type with
    __init__ + plain methods + annotated fields and @property accessors as
    getset. Reject the constructs the glue does not yet emit (rather than
    silently dropping them), and require every crossing field/param/return
    to marshal.
    """
    from ..type_def_registry import (
        is_boundary_marshallable, is_function_boundary_marshallable,
        is_span_boundary_param, is_internal_boundary_field,
        is_exposed_class, _boundary_inner,
        boundary_type_name, boundary_unmarshallable_msg)
    info = compiled.analyzer.registry.get_record(record.name)
    class_line = record.loc.line if record.loc else None

    def line_of(loc) -> 'int | None':
        return loc.line if loc is not None else class_line

    def reject(msg: str, loc=None) -> None:
        raise CompileError(f"@export class '{record.name}': {msg}",
                           compiled.name, compiled.path,
                           lineno=line_of(loc))

    # Exception classes already cross via the PyErr_NewException path
    # (auto-exposed, no @export needed); the two type-creation paths are
    # disjoint, so @export on a throwable would double-create.
    if info.inherits_base_exception or info.implements_throwable:
        reject("exception classes are exposed automatically -- remove @export")
    if info.type_params:
        reject("generic classes cannot be exposed yet (the class must be "
               "non-generic)")

    reg = compiled.analyzer.registry

    # Inheritance: a single exposed same-module base wires as the CPython
    # tp_base (real MRO inheritance of the base's methods/getsets/slots).
    # Multiple bases can never cross -- CPython rejects two bases with
    # distinct C instance layouts ("multiple bases have instance lay-out
    # conflict") -- and a non-@export base has no CPython type to wire.
    if len(info.parents) > 1:
        reject("multiple inheritance cannot be exposed (CPython allows "
               "only one base with a C instance layout)")
    for parent in info.parents:
        pinfo = reg.get_record_for_type(parent)
        pname = pinfo.name if pinfo is not None else str(parent)
        if not is_exposed_class(parent):
            reject(f"base class '{pname}' is not exposed -- @export the "
                   f"base too (an exposed class can only inherit an "
                   f"exposed class)")
        if reg.imported_record_qualification_for_type(
                parent, compiled.analyzer.ctx.module_name) is not None:
            reject(f"base class '{pname}' is defined in another module, "
                   f"which cannot cross the boundary yet (cross-module "
                   f"exposed types are deferred -- define and @export "
                   f"the base in this module)")

    # A declared @dynamic protocol base becomes a virtual C++ base: the
    # vptr displaces the base subobject the boundary's Instance<T> casts
    # rely on (rejected by a static_assert in class_bridge.hpp; this is
    # the located diagnostic in front of it).
    for proto in info.implemented_protocols:
        if proto.is_dynamic_protocol:
            reject(f"implementing @dynamic protocol '{proto.name}' cannot "
                   f"be exposed (the virtual-dispatch layout is "
                   f"incompatible with the CPython instance embedding)")

    def check(typ, what: str, role: str, loc=None, field_ctx=None) -> None:
        form_err = _exposed_form_error(typ, role, reg, compiled,
                                            field_ctx)
        if form_err is not None:
            reject(f"{what} {form_err}", loc)
        # Method/__init__ params and returns share the free-function glue
        # (_emit_marshal_in/_emit_call_return), so they admit the same
        # boundary set: containers recursively, and Span[T] as a param
        # only (buffer-protocol copy-in has no return direction). A getset
        # FIELD keeps the scalar-only base admission -- the per-field
        # getter/setter path has no container emit.
        if role == "field":
            if is_boundary_marshallable(typ, False):
                return
            if field_ctx is not None and exposed_view_field(
                    field_ctx[0], field_ctx[1], reg) is not None:
                # Crosses as a read-only borrow-view getset (the form
                # check above already rejected the cross-module case,
                # which the shared registry's exposed_to_host alone
                # can't distinguish).
                return
        elif ((role == "param" and is_span_boundary_param(typ))
                or is_function_boundary_marshallable(typ, role == "return")):
            return
        raise CompileError(
            boundary_unmarshallable_msg(
                record.name, what, boundary_type_name(typ), kind="class"),
            compiled.name, compiled.path, lineno=line_of(loc))

    for fld in info.fields:
        if is_internal_boundary_field(fld.name):
            continue  # internal payload state; not exposed, any type allowed
        check(fld.type, f"field '{fld.name}'", "field", fld.loc,
              field_ctx=(fld, info))

    # AST nodes (not the FunctionInfo overloads) carry the arg-form facts
    # the keyword-aware unpack can't cross (defaults/*args/**kwargs/posonly/
    # kwonly) and the method's own source location; map by name so the
    # per-method loop can read both. First node per name is enough -- the
    # form facts are signature-level, and an overloaded name is rejected
    # before its form is inspected.
    ast_by_name: dict = {}
    for rm in record.methods:
        ast_by_name.setdefault(rm.name, rm)

    for mname, overloads in info.methods.items():
        if mname.startswith("__") and mname.endswith("__") \
                and mname != "__init__":
            # Supported dunders cross as type slots and are validated by
            # sema_validators.validate_export_class_dunders; unsupported ones stay
            # off the host type (warned there too).
            continue
        ast_m = ast_by_name.get(mname)
        m_loc = ast_m.loc if ast_m is not None else None
        # The glue emits one positional wrapper per method; an @overload
        # group would silently expose only the first signature's arity.
        if len(overloads) > 1:
            reject(f"overloaded '{mname}' cannot be exposed yet (only a "
                   f"single signature crosses to CPython)", m_loc)
        # Every non-dunder registry method has a backing AST node (the only
        # AST-less registry entry is the synthesized `__iter__`, a dunder
        # already `continue`d above), so gating the shape/param-form checks
        # on `ast_m` never skips a method that should be validated.
        if ast_m is not None:
            # Decorator/kind/async/error-return forms the glue can't emit,
            # shared with the dunder validator (interop/export_shape) so the
            # two can't drift -- the shape message already names the method.
            shape = export_method_shape_error(ast_m)
            if shape is not None:
                reject(shape, m_loc)
            form = unsupported_boundary_param_form(ast_m)
            if form is not None:
                reject(f"method '{mname}': {form}", m_loc)
        for m in overloads:
            # __init__'s "return" is the constructed instance (no value
            # marshalled out); a plain method marshals its return, with
            # `-> None` handed back as None.
            if mname != "__init__":
                check(m.return_type, f"method '{mname}' return", "return", m_loc)
            for p in m.params:
                if p.name == "self":
                    continue
                check(p.type, f"method '{mname}' parameter '{p.name}'",
                      "param", m_loc)
            _warn_copy_boundary_mutation(
                compiled,
                f"exposed class '{record.name}' method '{mname}'", m,
                [(i, (p.name, p.type)) for i, p in enumerate(m.params)
                 if p.name != "self"],
                m_loc)

    # Properties cross as computed getset: the getter is a zero-arg method
    # return site, the setter a one-param method param site -- same
    # admission, no property-specific boundary set (two setter carve-outs
    # below, both because a setter's purpose is STORING its value).
    for pname, prop in info.properties.items():
        if is_internal_boundary_field(pname):
            continue  # `_`-named property: payload-only, never an attribute
        accessors = [rm for rm in record.methods if rm.name == pname]
        p_loc = accessors[0].loc if accessors else None
        for rm in accessors:
            shape = export_method_shape_error(rm, allow_property=True)
            if shape is not None:
                reject(shape, rm.loc)
        check(prop.getter.return_type, f"property '{pname}'", "return",
              p_loc)
        if prop.setter is None:
            continue
        s_loc = next((rm.loc for rm in accessors if rm.is_property_setter),
                     p_loc)
        # The parser guarantees a setter takes exactly one value param.
        sp = next(p for p in prop.setter.params if p.name != "self")
        if is_exposed_class(sp.type):
            # Every class-typed setter param is an ownership transfer (the
            # property Own auto-wrap runs before a user record's ValueType
            # flag is known, so value classes are wrapped too), but a class
            # value arrives as a borrow of the live argument payload --
            # nothing to move from. The generic Own[Cls] message would
            # advise "use the borrow form", which a setter cannot spell.
            cls_name = _boundary_inner(sp.type).name
            reject(f"property '{pname}' setter takes exposed class "
                   f"'{cls_name}': the value arrives as a borrow of the "
                   f"live argument, which the setter's ownership-transfer "
                   f"parameter cannot move from -- use a plain method",
                   s_loc)
        if is_span_boundary_param(sp.type):
            # A method Span param reads a caller buffer for the call's
            # duration; a setter's canonical body STORES its value, and the
            # buffer copy-in's backing vector dies when the wrapper
            # returns -- the stored span would dangle.
            reject(f"property '{pname}' setter cannot take a Span (the "
                   f"buffer copy-in lives only for the call; store "
                   f"list[T] instead)", s_loc)
        check(sp.type, f"property '{pname}' setter value", "param", s_loc)

def _exposed_form_error(typ, role: str, registry,
                        compiled: 'CompiledModule',
                        field_ctx=None) -> 'str | None':
    """Diagnose an exposed-class or exposed-enum boundary type the glue
    cannot emit, so a located error replaces an opaque C++ failure. role in
    {field,param,return}. Returns the message tail or None.

      - a getset *field* of exposed-class type: the getter would need
        per-instance marshalling of a nested class (deferred);
      - an `Own[Cls]` *param*: the host keeps its reference, so ownership
        can't transfer -- the Own ABI (`Cls&&`) can't bind the borrowed
        payload;
      - a `@nocopy` class *returned by reference* (`-> Cls`): the boundary
        copies the value out, but the copy ctor is deleted.
      - an exposed enum defined in *another* module: its CPython type handle
        lives in that module's glue, not this one, so the value marshallers
        here have nothing to reference (cross-module exposed types deferred).
      - an exposed enum as a getset *field*: the getset path marshals scalars
        via to_py/from_py, which have no enum overload (enums cross only as
        function params/returns, via enum_to_py/enum_from_py).
      - an exposed class defined in *another* module: same story as the enum
        case -- the class's qualified C++ name and CPython type handle are
        keyed to the defining module's glue, not this one.
    """
    from ..typesys import OwnType, TupleType
    from ..type_def_registry import (
        is_exposed_class, is_exposed_enum, _boundary_inner, enum_info_of,
        _container_element_types)
    module_name = compiled.analyzer.ctx.module_name

    def _unsupported_exposed_element(t, depth: int = 0) -> 'str | None':
        # Element shapes the per-element marshaller can't emit yet -- reject
        # with a located error instead of an opaque C++ failure. depth 0 = a
        # direct container element; depth > 0 = nested inside one. A copyable
        # local exposed class/enum as a direct list/tuple(enum)/dict-value
        # element crosses; the shapes below don't.
        inner_t = _boundary_inner(t)
        sub = _container_element_types(inner_t)
        if sub is None:
            return None
        is_tuple = isinstance(inner_t, TupleType)
        for et in sub:
            ei = _boundary_inner(et)
            if is_exposed_class(ei):
                info = registry.get_record_for_type(ei)
                cls = info.name if info is not None else "the class"
                if info is not None and info.is_nocopy:
                    return (f"has a @nocopy exposed-class container element "
                            f"'{cls}', which cannot cross -- each element is "
                            f"copied at the boundary, but @nocopy forbids the "
                            f"copy")
                if depth > 0:
                    return (f"has an exposed-class element '{cls}' nested "
                            f"inside a container element, which is not "
                            f"supported yet -- only a top-level container "
                            f"element crosses (flatten the nesting)")
                if is_tuple:
                    return (f"has an exposed-class tuple element '{cls}', which "
                            f"is not supported yet -- a tuple's non-value "
                            f"element uses borrow form (use a list/dict of the "
                            f"class, or a value-type/enum tuple element)")
            elif is_exposed_enum(ei):
                if depth > 0:
                    return ("has an exposed-enum element nested inside a "
                            "container element, which is not supported yet -- "
                            "only a top-level container element crosses "
                            "(flatten the nesting)")
            else:
                nested = _unsupported_exposed_element(et, depth + 1)
                if nested is not None:
                    return nested
        return None

    def _foreign_exposed_element(t) -> 'str | None':
        # A container element that is a cross-module exposed enum/class has
        # no handle in this glue TU (same reason a top-level cross-module
        # param is rejected) -- recurse so a nested element is caught too.
        sub = _container_element_types(_boundary_inner(t))
        if sub is None:
            return None
        for et in sub:
            ei = _boundary_inner(et)
            if is_exposed_enum(ei):
                einfo = enum_info_of(ei)
                if (einfo is not None and einfo.module_name is not None
                        and einfo.module_name != module_name):
                    return ("has an exposed-enum container element from "
                            "another module, which cannot cross the boundary "
                            "yet (cross-module exposed types are deferred -- "
                            "define and @export the enum in this module)")
            elif is_exposed_class(ei):
                if registry.imported_record_qualification_for_type(
                        ei, module_name) is not None:
                    return ("has an exposed-class container element from "
                            "another module, which cannot cross the boundary "
                            "yet (cross-module exposed types are deferred -- "
                            "define and @export the class in this module)")
            else:
                nested = _foreign_exposed_element(et)
                if nested is not None:
                    return nested
        return None

    container_err = _foreign_exposed_element(typ)
    if container_err is not None:
        return container_err

    unsupported_err = _unsupported_exposed_element(typ)
    if unsupported_err is not None:
        return unsupported_err

    if is_exposed_enum(typ):
        # Only enums DEFINED + @export-ed in this module get a handle in this
        # glue TU; an imported exposed enum is admitted by the shared
        # boundary_marshal fact but would reference an undeclared handle.
        # Resolve locality from the TYPE OBJECT's own EnumInfo.module_name
        # (set at registration to the defining module, `None` for an
        # entry-point-defined enum), not `registry.imported_enum_-
        # qualification` -- that helper looks up `self.enums` by the name
        # ARGUMENT it's given, i.e. the LOCAL BINDING name in this module,
        # so passing the type's own canonical `.name` (which ignores a
        # local import alias) can resolve to an unrelated same-named LOCAL
        # enum instead of the actual foreign one -- exactly the collision
        # this guard exists to catch.
        einfo = enum_info_of(_boundary_inner(typ))
        if (einfo is not None and einfo.module_name is not None
                and einfo.module_name != module_name):
            return ("is an exposed enum from another module, which cannot "
                    "cross the boundary yet (cross-module exposed types are "
                    "deferred -- define and @export the enum in this module)")
        # A locally-defined exposed enum IS a valid getset field: enums are
        # value types with interned singleton members, so the getset copy
        # (enum_to_py reconstructs the member via `EnumType(value)`) is
        # identity-preserving -- no aliasing divergence, unlike a class field.
        return None
    if not is_exposed_class(typ):
        return None
    info = registry.get_record_for_type(_boundary_inner(typ))
    cls = info.name if info is not None else "the class"
    # Same reasoning as the enum case above: only a class DEFINED +
    # @export-ed in this module gets a struct + type handle in this glue TU;
    # an imported exposed class is admitted by the shared boundary_marshal
    # fact but _class_cpp_var would qualify it under the wrong module's
    # namespace and reference an undeclared handle. `imported_record_-
    # qualification_for_type` (the same qname-aware helper codegen uses to
    # qualify a cross-module record) is what answers "is this local?"
    # correctly -- a short-name-only check would wrongly call a foreign
    # class local whenever this module also happens to define its own
    # same-named exposed class.
    if registry.imported_record_qualification_for_type(
            _boundary_inner(typ), module_name) is not None:
        return ("is an exposed class from another module, which cannot "
                "cross the boundary yet (cross-module exposed types are "
                "deferred -- define and @export the class in this module)")
    if role == "field" and not _boundary_inner(typ).is_value_type():
        # A never-reassigned reference-class field crosses as a READ-ONLY
        # borrow-view getset: the getter mints a PyObject aliasing the
        # live field (owner keepalive; registry-deduped identity), so
        # `outer.inner.x = 5` writes through like CPython. The gate is
        # the reassignment: a view aliases the storage SLOT, so a field
        # rebound after __init__ would show the replacement through a
        # live view where CPython's rebind leaves the old object intact
        # -- those (and @nocopy fields) keep the located reject below.
        # A value-type field is exempt from all of this: value types are
        # immutable, so copy-out is behaviorally invisible (read-only
        # getset; a mutation attempt loud-fails) -- it falls through to
        # admission below, like an exposed-enum field.
        if field_ctx is not None:
            fld, declarer = field_ctx
            if exposed_view_field(fld, declarer, registry) is not None:
                return None
        return (f"of exposed-class type '{cls}' cannot be exposed as a "
                f"getset field: only a field never reassigned outside "
                f"__init__ (and not @nocopy) crosses as a read-only "
                f"aliasing view -- a reassignable field's view would read "
                f"the storage slot through the reassignment where CPython "
                f"keeps the old object. Drop the post-__init__ "
                f"reassignment, or make the field internal by prefixing "
                f"its name with '_' (kept as payload state, never a "
                f"Python attribute) and expose a method that mutates it "
                f"through 'self' or returns an explicit copy()")
    if role == "param" and isinstance(typ, OwnType):
        return (f"is Own[{cls}], which is not a valid boundary type: the host "
                f"keeps its reference, so ownership cannot transfer -- use "
                f"the borrow form '{cls}'")
    if role == "return":
        # Shared with the dunder validator; peels every borrow spelling
        # (RefType, readonly-topped, the property getter's un-normalized
        # bare class), so none of them reaches the C++ deleted-copy error.
        nocopy_err = nocopy_borrow_return_error(typ, registry)
        if nocopy_err is not None:
            return nocopy_err
    return None
