"""CPython extension glue emitter.

Emits the glue TU for an `# tpy: ext_module` -- the `PyMethodDef` /
`PyModuleDef` / `PyInit_` boilerplate plus one wrapper per `@export`-ed free
function, the per-exposed-class `PyType_FromSpec` machinery (method/getset
wrappers, slot tables, `tp_init`), and the `PyErr_NewException` types for
user exception classes. The TU never includes `Python.h`: every C-API call
goes through the `tpy/interop` facade.

It is a self-contained sibling of the main `(hpp, cpp)` codegen
(`generator.py`): an independent backend concern that grows as the boundary
gains types/forms, kept apart from the load-bearing generator.
"""
from __future__ import annotations
import io
from typing import TYPE_CHECKING, TextIO

from ..parse import TpyModule, TpyVarDecl
from ..typesys import (
    TpyType, is_void_like_type, FinalType, OwnType, ReadonlyType)
from ..type_def_registry import (
    is_boundary_marshallable, is_function_boundary_marshallable, is_exposed_class,
    is_exposed_enum, is_span_boundary_param, _boundary_inner, enum_info_of,
    is_str_view_type, boundary_cpp_type, boundary_type_name,
    boundary_unmarshallable_msg, is_internal_boundary_field,
    _container_element_types, is_list, is_dict, is_set,
)
from ..modules import BINOP_TO_METHOD, BINOP_TO_RMETHOD, AUGOP_TO_IMETHOD, UNARYOP_TO_METHOD
from .context import (
    qualified_cpp_name, escape_cpp_name, module_to_include_path, CodeGenError)
from .type_resolution import resolve_stmt_type_cascade

if TYPE_CHECKING:
    from .context import CodeGenContext
    from .records import RecordGenerator
    from .types import TypeResolver
    from ..typesys import RecordInfo

# Exposed-class arithmetic/ordering operators: dunder name
# -> CPython nb_* slot id. Dunder NAMES come from the canonical
# BINOP_TO_METHOD/BINOP_TO_RMETHOD/AUGOP_TO_IMETHOD/UNARYOP_TO_METHOD tables
# (modules/defs.py), so this stays in sync with the non-exposed operator path
# by construction; only the CPython slot id (new here) is added per symbol.
_NB_BINARY_SYMBOL_SLOT = {
    "+": "Py_nb_add", "-": "Py_nb_subtract", "*": "Py_nb_multiply",
    "div": "Py_nb_true_divide", "//": "Py_nb_floor_divide", "%": "Py_nb_remainder",
    "**": "Py_nb_power", "<<": "Py_nb_lshift", ">>": "Py_nb_rshift",
    "&": "Py_nb_and", "|": "Py_nb_or", "^": "Py_nb_xor",
}
_NB_UNARY_SYMBOL_SLOT = {
    "+": "Py_nb_positive", "-": "Py_nb_negative", "~": "Py_nb_invert",
}
_NB_INPLACE_SYMBOL_SLOT = {
    "+": "Py_nb_inplace_add", "-": "Py_nb_inplace_subtract", "*": "Py_nb_inplace_multiply",
    "div": "Py_nb_inplace_true_divide", "//": "Py_nb_inplace_floor_divide",
    "%": "Py_nb_inplace_remainder", "<<": "Py_nb_inplace_lshift", ">>": "Py_nb_inplace_rshift",
    "&": "Py_nb_inplace_and", "|": "Py_nb_inplace_or", "^": "Py_nb_inplace_xor",
}
# dunder -> (slot id, reflected dunder or None)
_NB_BINARY_OPS = {
    BINOP_TO_METHOD[sym]: (slot, BINOP_TO_RMETHOD.get(sym))
    for sym, slot in _NB_BINARY_SYMBOL_SLOT.items()
}
_NB_UNARY_OPS = {
    UNARYOP_TO_METHOD[sym]: slot for sym, slot in _NB_UNARY_SYMBOL_SLOT.items()
}
_NB_INPLACE_OPS = {
    AUGOP_TO_IMETHOD[sym]: slot for sym, slot in _NB_INPLACE_SYMBOL_SLOT.items()
}


class ExtensionGenerator:
    """Emits the CPython extension glue TU for an `# tpy: ext_module`."""

    def __init__(self, ctx: CodeGenContext, records: RecordGenerator,
                 types: TypeResolver):
        self.ctx = ctx
        self.records = records  # shares the inheritance topo sort for exc ordering
        self.types = types  # resolves exposed-constant (Final global) types

    def _user_exc_base_name(self, info, sibling_names: set) -> str | None:
        """Simple name of this exception's immediate base if that base is another user exception class in this module; else None (built-in base)."""
        if not info.parents:
            return None
        base_info = self.ctx.analyzer.registry.get_record_for_type(info.parents[0])
        if base_info is None or base_info.is_native:
            return None
        return base_info.name if base_info.name in sibling_names else None

    def _ordered_user_exc_classes(self, module: TpyModule, module_name: str,
                                  call_ns: str) -> list[dict]:
        """User exception classes DEFINED in this module, ordered base-before-
        derived (a user base's Python type must exist before PyErr_NewException
        references it). Each entry drives one type created + registered at
        PyInit_. base_expr is the NewException base: a sibling user exc's var,
        or py_exc_by_name("<builtin>") for a built-in base.
        """
        reg = self.ctx.analyzer.registry
        exc_records = [
            r for r in module.records
            if (info := reg.get_record(r.name)) is not None and not info.is_native
            and info.implements_throwable and info.inherits_base_exception
        ]
        if not exc_records:
            return []
        # Parent-before-child via the shared records-emit topo sort (a user exc
        # only inherits other exc classes, so every user parent is in this set).
        ordered = self.records.sort_records_by_inheritance(exc_records)
        sibling_names = {r.name for r in exc_records}
        sym = escape_cpp_name(module_name)
        var_for = lambda nm: f"{sym}__exc_{escape_cpp_name(nm.replace('.', '_'))}"

        result = []
        for record in ordered:
            info = reg.get_record(record.name)
            base_user = self._user_exc_base_name(info, sibling_names)
            if base_user is not None:
                base_expr = var_for(base_user)
            else:
                base_simple = (info.parents[0].qualified_name().split(".")[-1]
                               if info.parents else "Exception")
                base_expr = f'::tpy::interop::py_exc_by_name("{base_simple}")'
            # A data-carrying exc (data fields beyond the base message, own OR
            # inherited from a user exc base) gets a generated setter marshalling
            # each field to an attribute; a message-only exc reuses the shared
            # default setter. `_`-named fields are internal payload state and
            # never cross as attributes, so an exc carrying only those is
            # message-only.
            fields = [f for f in reg.user_declared_fields(info)
                      if not is_internal_boundary_field(f.name)]
            has_data = bool(fields)
            setter = (var_for(record.name) + "_seterr" if has_data
                      else "::tpy::interop::exc_set_err_message_only")
            result.append({
                "var": var_for(record.name),
                "cpp_type": qualified_cpp_name(call_ns, record.name),
                "py_name": f"{module_name}.{record.name}",
                "base_expr": base_expr,
                "fields": fields,
                "has_data": has_data,
                "setter": setter,
            })
        return result

    def _emit_user_exc_setters(self, out: TextIO, user_excs: list[dict],
                               sym: str) -> None:
        """One setter per data-carrying user exception: reconstruct a Python
        instance from the message (`pytype(e.what())`), marshal each data field
        to an instance attribute, then PyErr_SetObject. str()/args reflect the
        message only -- the C++ exception holds fields, not the original
        constructor arg tuple, so the full args cannot be reconstructed (an
        acknowledged divergence). Field marshal reuses the getset/operator
        single-value helper, so a scalar/str/bytes/enum field round-trips."""
        for e in user_excs:
            if not e["has_data"]:
                continue
            cpp = e["cpp_type"]
            out.write(f'void {e["setter"]}(const ::tpy::BaseException &__base, '
                      "PyObject *__pytype) noexcept {\n")
            # Exact dynamic type matched in the registry, so the downcast is safe.
            out.write(f"    const auto &__e = static_cast<const {cpp} &>(__base);\n")
            # A field marshaller (e.g. a BigInt scalar's to_py) can throw under
            # OOM; the setter is noexcept, so a leaked exception would terminate
            # the host. Catch and degrade to a no-alloc MemoryError.
            out.write("    PyObject *__inst = nullptr;\n")
            out.write("    try {\n")
            out.write('        PyObject *__args = ::tpy::cpy::Py_BuildValue("(s)", '
                      "__e.what());\n")
            out.write("        if (!__args) return;\n")
            out.write("        __inst = ::tpy::cpy::PyObject_Call("
                      "__pytype, __args, nullptr);\n")
            out.write("        ::tpy::cpy::Py_DecRef(__args);\n")
            out.write("        if (!__inst) return;\n")
            for fld in e["fields"]:
                vexpr = self._value_out_expr(
                    f"__e.{escape_cpp_name(fld.name)}", fld.type, sym)
                out.write(f"        {{ PyObject *__v = {vexpr};\n")
                out.write("          if (!__v) { ::tpy::cpy::Py_DecRef(__inst); "
                          "return; }\n")
                out.write("          if (::tpy::cpy::PyObject_SetAttrString(__inst, "
                          f'"{fld.name}", __v) < 0) {{\n')
                out.write("            ::tpy::cpy::Py_DecRef(__v); "
                          "::tpy::cpy::Py_DecRef(__inst); return; }\n")
                out.write("          ::tpy::cpy::Py_DecRef(__v); }\n")
            out.write("        ::tpy::cpy::PyErr_SetObject(__pytype, __inst);\n")
            out.write("        ::tpy::cpy::Py_DecRef(__inst);\n")
            out.write("    } catch (...) {\n")
            out.write("        if (__inst) ::tpy::cpy::Py_DecRef(__inst);\n")
            out.write("        if (!::tpy::cpy::PyErr_Occurred()) "
                      "::tpy::cpy::PyErr_NoMemory();\n")
            out.write("    }\n")
            out.write("}\n\n")

    def _exposed_classes(self, module: TpyModule, module_name: str,
                         call_ns: str) -> list[dict]:
        """User classes marked `@export` in this ext_module. Each drives one
        PyType_FromSpec(WithBases) type created + registered at PyInit_ and one
        set of method/getset wrappers. (Disjoint from _ordered_user_exc_classes:
        the sema validator rejects @export on a throwable.)

        Ordered base-before-derived (a derived type's creation call references
        its base's already-created handle); classes without an exposed base
        keep source order, so a flat module's glue is unchanged. `base_simple`/
        `base_var` name the single exposed base (the validator rejected
        multiple / unexposed / cross-module bases); `basetype` marks a class
        some other exposed class inherits -- its spec carries
        Py_TPFLAGS_BASETYPE and its tp_init the exact-type guard."""
        reg = self.ctx.analyzer.registry
        sym = escape_cpp_name(module_name)
        exposed = [r for r in module.records if r.exposed_to_host]
        by_name = {r.name: r for r in exposed}
        base_of: dict[str, str | None] = {}
        for r in exposed:
            info = reg.get_record(r.name)
            parent = None
            for p in info.parents:
                pinfo = reg.get_record_for_type(p)
                if pinfo is not None and pinfo.name in by_name:
                    parent = pinfo.name
            base_of[r.name] = parent
        base_names = set(base_of.values())

        ordered: list = []
        seen: set[str] = set()

        def visit(r) -> None:
            if r.name in seen:
                return
            seen.add(r.name)
            base = base_of[r.name]
            if base is not None:
                visit(by_name[base])
            ordered.append(r)

        for r in exposed:
            visit(r)

        result = []
        for r in ordered:
            base = base_of[r.name]
            result.append({
                "record": r,
                "info": reg.get_record(r.name),
                "var": f"{sym}__type_{escape_cpp_name(r.name)}",
                "cpp_type": qualified_cpp_name(call_ns, r.name),
                "py_name": f"{module_name}.{r.name}",
                "simple": r.name,
                "base_simple": base,
                "base_var": (f"{sym}__type_{escape_cpp_name(base)}"
                             if base is not None else None),
                "basetype": r.name in base_names,
            })
        return result

    def _exposed_enums(self, module: TpyModule, module_name: str) -> list[dict]:
        """`@export` enums DEFINED at module top level. Each is recreated as a
        CPython IntEnum/Enum at PyInit_ from its (name, value) member table.
        (The sema validator rejected @export on @native and nested enums.)"""
        reg = self.ctx.analyzer.registry
        sym = escape_cpp_name(module_name)
        result = []
        for e in module.enums:
            if not e.exposed_to_host:
                continue
            underlying = enum_info_of(reg.get_enum(e.name)).underlying_type.to_cpp()
            result.append({
                "simple": e.name,
                "py_name": f"{module_name}.{e.name}",
                "var": f"{sym}__enum_{escape_cpp_name(e.name)}",
                "is_int_enum": e.is_int_enum,
                "underlying": underlying,
                "members": [(name, value) for name, value, _loc in e.members],
            })
        return result

    def _constant_exposable(self, inner: TpyType) -> bool:
        # StrView is admitted (a Final[str] resolves to it) because the constant
        # is output-only -- to_py copies the static-literal view at init, so the
        # borrow-lifetime reason StrView is rejected as a *param* does not apply.
        return is_boundary_marshallable(inner, False) or is_str_view_type(inner)

    def _exposed_constants(self, module: TpyModule, call_ns: str) -> list[dict]:
        """Module-level `Final` constants of an exposable type, surfaced as
        init-time module-attribute snapshots (Final => immutable, so the snapshot
        can't go stale). Dunder bindings (the auto-injected `__name__` etc.) are
        module internals, not user constants, so they are skipped."""
        result = []
        seen: set[str] = set()
        for stmt in module.top_level_stmts:
            if not isinstance(stmt, TpyVarDecl) or not stmt.is_final:
                continue
            if stmt.name in seen or (stmt.name.startswith("__")
                                     and stmt.name.endswith("__")):
                continue
            var_type = resolve_stmt_type_cascade(stmt, self.ctx.analyzer, self.types)
            if var_type is None:
                continue
            inner = var_type.wrapped if isinstance(var_type, FinalType) else var_type
            if not self._constant_exposable(inner):
                continue
            seen.add(stmt.name)
            result.append({
                "py_name": stmt.name,
                "cpp_ref": qualified_cpp_name(call_ns, stmt.name),
            })
        return result

    def _class_cpp_var(self, typ: TpyType, sym: str) -> tuple[str, str]:
        """For an exposed-class param/return type (after stripping Own/Ref),
        the qualified C++ struct name and the module-static PyObject* holding
        its CPython type. The class is defined in this module (cross-module
        exposed classes are deferred), so the cpp name matches the one used at
        type creation (Instance<T> / PyType_FromSpec)."""
        info = self.ctx.analyzer.registry.get_record_for_type(_boundary_inner(typ))
        cpp = qualified_cpp_name(self.ctx.module_name, info.name)
        return cpp, f"{sym}__type_{escape_cpp_name(info.name)}"

    def _enum_cpp_var(self, typ: TpyType, sym: str) -> tuple[str, str]:
        """For an exposed-enum param/return type, the qualified C++ `enum class`
        name and the module-static PyObject* holding its CPython enum type. The
        enum is defined in this module (cross-module is rejected by the
        validator), so the cpp name and handle match the type-creation site."""
        name = _boundary_inner(typ).name
        cpp = qualified_cpp_name(self.ctx.module_name, name)
        return cpp, f"{sym}__enum_{escape_cpp_name(name)}"

    def _emit_arg_unpack(self, out: TextIO, param_names: list[str],
                         fail_ret: str, fn_label: str) -> None:
        """Emit the keyword-aware unpack prologue for a wrapper with >=1 param:
        the kwlist of Python param names, the borrowed-PyObject* arg locals, and
        the PyArg_ParseTupleAndKeywords call -- giving the exposed callable
        Python's positional-or-keyword semantics. `fail_ret` is returned on a
        parse failure ("nullptr" for the wrapper sentinel, "-1" for tp_init).
        Reads `args`/`kwargs` (the wrapper's param names). Caller guarantees a
        non-empty param list (a zero-arg callable stays METH_NOARGS).

        `fn_label` is appended to the format string as the C-API `:name`
        suffix, so a parse error (wrong arity, unknown keyword) names the
        callable -- closer to CPython's own `func() got ...` wording, though
        the residual text still differs (the C parser's message phrasing).
        """
        n = len(param_names)
        kw = ", ".join(f'const_cast<char *>("{nm}")' for nm in param_names)
        decls = " ".join(f"PyObject *a{i} = nullptr;" for i in range(n))
        addrs = ", ".join(f"&a{i}" for i in range(n))
        out.write(f"    static char *__kwlist[] = {{{kw}, nullptr}};\n")
        out.write(f"    {decls}\n")
        out.write(f'    if (!PyArg_ParseTupleAndKeywords(args, kwargs, '
                  f'"{"O" * n}:{fn_label}", __kwlist, {addrs})) '
                  f"return {fail_ret};\n")

    def _marshal_in_expr(self, typ: TpyType, src: str, depth: int) -> str:
        """A C++ expression converting the borrowed PyObject* `src` into the TPy
        value of `typ`. Recurses for containers -- the glue, not a C++ template,
        drives the element choice because the C++ type is ambiguous at the leaves
        (list[bytes] and list[list[UInt8]] share a C++ type) -- and bottoms out
        at from_py<leaf>."""
        inner = _boundary_inner(typ)
        elems = _container_element_types(inner)
        if elems is None:
            # A locally-exposed enum/class element marshals through its module
            # type handle, exactly like a top-level param -- copy-in into the
            # container's stored element. (Cross-module elements are rejected in
            # the validator, so the handle is always this module's.)
            if is_exposed_class(inner):
                cpp, tv = self._class_cpp_var(inner, escape_cpp_name(self.ctx.module_name))
                return (f"*::tpy::interop::instance_payload<{cpp}>("
                        f"{src}, (::tpy::cpy::PyTypeObject *){tv})")
            if is_exposed_enum(inner):
                cpp, ev = self._enum_cpp_var(inner, escape_cpp_name(self.ctx.module_name))
                return f"::tpy::interop::enum_from_py<{cpp}>({src}, {ev})"
            return f"::tpy::interop::from_py<{boundary_cpp_type(inner)}>({src})"
        if is_list(inner):
            return (f"::tpy::interop::list_from_py<{self._elem_cpp(elems[0])}>("
                    f"{src}, {self._in_lambda(elems[0], depth)})")
        if is_set(inner):
            return (f"::tpy::interop::set_from_py<{self._elem_cpp(elems[0])}>("
                    f"{src}, {self._in_lambda(elems[0], depth)})")
        if is_dict(inner):
            return (f"::tpy::interop::dict_from_py<{self._elem_cpp(elems[0])}, "
                    f"{self._elem_cpp(elems[1])}>({src}, "
                    f"{self._in_lambda(elems[0], depth)}, "
                    f"{self._in_lambda(elems[1], depth)})")
        targs = ", ".join(self._elem_cpp(e) for e in elems)
        tfns = ", ".join(self._in_lambda(e, depth) for e in elems)
        return f"::tpy::interop::tuple_from_py<{targs}>({src}, {tfns})"

    def _in_lambda(self, et: TpyType, depth: int) -> str:
        var = f"__e{depth}"
        return (f"[](::tpy::cpy::PyObject *{var}) {{ return "
                f"{self._marshal_in_expr(et, var, depth + 1)}; }}")

    def _marshal_out_expr(self, typ: TpyType, src: str, depth: int) -> str:
        """A C++ expression producing a new PyObject* (nullptr on failure) from
        the TPy value `src`. Mirror of _marshal_in_expr; container leaves are
        scalar/str/bytes or a locally-exposed enum/class element."""
        inner = _boundary_inner(typ)
        elems = _container_element_types(inner)
        if elems is None:
            if is_exposed_class(inner):
                _cpp, tv = self._class_cpp_var(inner, escape_cpp_name(self.ctx.module_name))
                return (f"::tpy::interop::instance_to_py("
                        f"(::tpy::cpy::PyTypeObject *){tv}, {src})")
            if is_exposed_enum(inner):
                _cpp, ev = self._enum_cpp_var(inner, escape_cpp_name(self.ctx.module_name))
                und = enum_info_of(inner).underlying_type.to_cpp()
                return f"::tpy::interop::enum_to_py({ev}, static_cast<{und}>({src}))"
            return f"::tpy::interop::to_py({src})"
        if is_list(inner):
            return (f"::tpy::interop::list_to_py("
                    f"{src}, {self._out_lambda(elems[0], depth)})")
        if is_set(inner):
            return (f"::tpy::interop::set_to_py("
                    f"{src}, {self._out_lambda(elems[0], depth)})")
        if is_dict(inner):
            return (f"::tpy::interop::dict_to_py({src}, "
                    f"{self._out_lambda(elems[0], depth)}, "
                    f"{self._out_lambda(elems[1], depth)})")
        tfns = ", ".join(self._out_lambda(e, depth) for e in elems)
        return f"::tpy::interop::tuple_to_py({src}, {tfns})"

    def _out_lambda(self, et: TpyType, depth: int) -> str:
        var = f"__o{depth}"
        return (f"[](const {self._elem_cpp(et)} &{var}) {{ return "
                f"{self._marshal_out_expr(et, var, depth + 1)}; }}")

    def _elem_cpp(self, et: TpyType) -> str:
        # Stored form: a container holds owned elements (str -> std::string,
        # bytes -> std::vector<uint8_t>), matching what the container's own C++
        # render instantiates and what from_py<leaf>/to_py are keyed on. An
        # exposed class/enum element needs its NAMESPACE-QUALIFIED C++ name (the
        # same one the handle path uses) -- to_cpp_stored() yields the bare name,
        # which is not visible in the glue's anonymous namespace.
        inner = _boundary_inner(et)
        if is_exposed_class(inner):
            return self._class_cpp_var(inner, escape_cpp_name(self.ctx.module_name))[0]
        if is_exposed_enum(inner):
            return self._enum_cpp_var(inner, escape_cpp_name(self.ctx.module_name))[0]
        return inner.to_cpp_stored()

    def _container_has_exposed_element(self, typ: TpyType) -> bool:
        """Whether a container type has (recursively) an exposed enum/class
        element -- those render as bare namespace-local C++ names, so the glue
        must qualify them / declare the local via `auto` instead of the bare
        container render."""
        elems = _container_element_types(_boundary_inner(typ))
        if elems is None:
            return False
        for e in elems:
            ei = _boundary_inner(e)
            if is_exposed_class(ei) or is_exposed_enum(ei):
                return True
            if self._container_has_exposed_element(ei):
                return True
        return False

    def _span_elem_cpp(self, typ: TpyType) -> str:
        """The numeric C++ element type of a Span[T]/Span[readonly[T]] param
        (after stripping Own/Ref and readonly) -- the type span_from_py<T> is
        keyed on."""
        from ..typesys import ReadonlyType, unwrap_readonly
        elem = _boundary_inner(typ).type_args[0]
        if isinstance(elem, ReadonlyType):
            elem = unwrap_readonly(elem)
        return elem.to_cpp()

    def _emit_marshal_in(self, out: TextIO, idx: int, typ: TpyType,
                         sym: str) -> str:
        """Marshal arg a{idx} into a local; return the token to pass at the
        call. A class param binds a reference to the live embedded payload --
        the borrow that makes mutation through it write through to the same
        PyObject; a scalar/str/bytes param copies in via from_py; a container
        param copies in O(n) via the recursive glue; a Span[T] param copies in
        via the buffer protocol into a vector that implicitly converts to the
        function's span<T>/span<const T> param, the same "owned local outlives
        the call" trick str/bytes use for string_view/span<const uint8_t>."""
        if is_exposed_class(typ):
            cpp, tv = self._class_cpp_var(typ, sym)
            out.write(f"        {cpp} &__p{idx} = "
                      f"*::tpy::interop::instance_payload<{cpp}>("
                      f"a{idx}, (::tpy::cpy::PyTypeObject *){tv});\n")
        elif is_exposed_enum(typ):
            cpp, ev = self._enum_cpp_var(typ, sym)
            out.write(f"        {cpp} __p{idx} = "
                      f"::tpy::interop::enum_from_py<{cpp}>(a{idx}, {ev});\n")
        elif is_span_boundary_param(typ):
            elem_cpp = self._span_elem_cpp(typ)
            out.write(f"        std::vector<{elem_cpp}> __p{idx} = "
                      f"::tpy::interop::span_from_py<{elem_cpp}>(a{idx});\n")
        elif _container_element_types(_boundary_inner(typ)) is not None:
            if self._container_has_exposed_element(_boundary_inner(typ)):
                # An exposed class/enum element renders as a bare
                # (namespace-local) C++ name that doesn't resolve in the glue's
                # namespace; let the marshaller's qualified return type drive the
                # local via `auto` (it is the same std::vector<...> the callee's
                # in-namespace signature names).
                out.write(f"        auto __p{idx} = "
                          f"{self._marshal_in_expr(typ, f'a{idx}', 0)};\n")
            else:
                cpp = boundary_cpp_type(_boundary_inner(typ))
                out.write(f"        {cpp} __p{idx} = "
                          f"{self._marshal_in_expr(typ, f'a{idx}', 0)};\n")
        else:
            cpp = boundary_cpp_type(_boundary_inner(typ))
            out.write(f"        {cpp} __p{idx} = "
                      f"::tpy::interop::from_py<{cpp}>(a{idx});\n")
        return f"__p{idx}"

    def _param_alias_candidates(
            self, params: list[tuple[str, TpyType]]
    ) -> list[tuple[str, str, 'RecordInfo']]:
        """(payload_expr, pyobj_expr, RecordInfo) for each exposed-class
        param -- the boundary-crossed objects a borrow return could hand
        back by identity (`__p{i}` is the payload reference
        `_emit_marshal_in` binds for the class param `a{i}`)."""
        reg = self.ctx.analyzer.registry
        out: list[tuple[str, str, 'RecordInfo']] = []
        for i, (_pn, t) in enumerate(params):
            if not is_exposed_class(t):
                continue
            cinfo = reg.get_record_for_type(_boundary_inner(t))
            if cinfo is not None:
                out.append((f"__p{i}", f"a{i}", cinfo))
        return out

    def _scoped_alias_candidates(
            self, ret_typ: TpyType,
            alias_candidates: list[tuple[str, str, 'RecordInfo']]
    ) -> list[tuple[str, str]]:
        """Filter (payload_expr, pyobj_expr, RecordInfo) alias candidates down
        to those whose declared class is inheritance-related to the return
        class. Only for related classes does address equality imply "same
        object": an UNRELATED exposed class can share the returned reference's
        address (a first `_`-internal field's payload starts at offset 0 of
        its holder), so comparing it could hand back the wrong PyObject."""
        reg = self.ctx.analyzer.registry
        rinfo = reg.get_record_for_type(_boundary_inner(ret_typ))
        if rinfo is None or rinfo.is_value_type:
            # A value-type class returns by value (a prvalue -- nothing to
            # address-match, and copying is its honest semantics anyway).
            return []
        return [(payload, pyobj) for payload, pyobj, cinfo in alias_candidates
                if cinfo is rinfo
                or reg.is_subclass_of_record(cinfo, rinfo)
                or reg.is_subclass_of_record(rinfo, cinfo)]

    def _emit_call_return(self, out: TextIO, ret_typ: TpyType | None,
                          call_expr: str, sym: str,
                          alias_candidates: tuple[tuple[str, str, 'RecordInfo'], ...] |
                          list[tuple[str, str, 'RecordInfo']] = ()) -> None:
        """Emit the return of a boundary call: void -> None; an exposed class
        borrow return -> the ORIGINAL PyObject when the returned reference's
        address matches a boundary-crossed candidate (self / an exposed-class
        param -- identity and write-through preserved, and a derived instance
        crosses un-sliced), else a fresh wrapping instance (instance_to_py);
        an Own[...] class return is always a fresh instance; else to_py."""
        if ret_typ is None or is_void_like_type(ret_typ):
            out.write(f"        {call_expr};\n")
            out.write("        return ::tpy::interop::none_to_py();\n")
        elif is_exposed_class(ret_typ):
            _cpp, tv = self._class_cpp_var(ret_typ, sym)
            # Borrow-form returns only: an Own[...] return is a fresh value
            # and can never alias a candidate. A reference-class return
            # without Own IS the borrow form (an owned return would require
            # Own[...]), whether or not the Ref wrapper survived on this
            # FunctionInfo (a property getter's return_type is the bare
            # class); value-type classes return by value (prvalue) and are
            # excluded in _scoped_alias_candidates.
            t = ret_typ
            while isinstance(t, ReadonlyType):
                t = t.wrapped
            scoped = ([] if isinstance(t, OwnType) else
                      self._scoped_alias_candidates(ret_typ, alias_candidates))
            if scoped:
                out.write(f"        auto &__r = {call_expr};\n")
                for payload, pyobj in scoped:
                    out.write(f"        if (&__r == &{payload}) {{ "
                              f"Py_IncRef({pyobj}); return {pyobj}; }}\n")
                out.write(f"        return ::tpy::interop::instance_to_py("
                          f"(::tpy::cpy::PyTypeObject *){tv}, __r);\n")
            else:
                out.write(f"        return ::tpy::interop::instance_to_py("
                          f"(::tpy::cpy::PyTypeObject *){tv}, {call_expr});\n")
        elif is_exposed_enum(ret_typ):
            _cpp, ev = self._enum_cpp_var(ret_typ, sym)
            # Cast to the enum's underlying int (not long long) so enum_to_py
            # picks the signed/unsigned Py_BuildValue format -- a UInt64 member
            # above INT64_MAX must cross unsigned, not wrap to a negative.
            und = enum_info_of(_boundary_inner(ret_typ)).underlying_type.to_cpp()
            out.write(f"        return ::tpy::interop::enum_to_py({ev}, "
                      f"static_cast<{und}>({call_expr}));\n")
        elif _container_element_types(_boundary_inner(ret_typ)) is not None:
            out.write(f"        return "
                      f"{self._marshal_out_expr(ret_typ, call_expr, 0)};\n")
        else:
            out.write(f"        return ::tpy::interop::to_py({call_expr});\n")

    def _emit_boundary_catch(self, out: TextIO, reg_arg: str) -> None:
        """The shared per-wrapper exception boundary: a body-raised TPy
        exception crosses with its type+message; anything else (incl. the
        marshaller's PyErr-presetting MarshalError) flows through the generic
        catch, which preserves an already-set Python error."""
        out.write("    } catch (const ::tpy::BaseException &__e) {\n")
        out.write(f"        ::tpy::interop::set_py_err_from(__e{reg_arg});\n")
        out.write("        return nullptr;\n")
        out.write("    } catch (...) {\n")
        out.write("        if (!PyErr_Occurred())\n")
        out.write('            PyErr_SetString(PyExc_RuntimeError, '
                  '"tpy extension: unhandled error");\n')
        out.write("        return nullptr;\n")
        out.write("    }\n")

    # richcompare op -> the CPython Py_LT..Py_GE constant + which dunder
    # supplies it. __ne__ alone falls back to `not __eq__` when the record
    # defines __eq__ but not __ne__ (mirrors sema/expressions.py's own
    # __eq__-implies-__ne__ fallback for the non-exposed operator path);
    # no other direction is auto-derived.
    _COMPARE_DUNDER_OPS = (
        ("__lt__", "Py_LT"), ("__le__", "Py_LE"), ("__eq__", "Py_EQ"),
        ("__ne__", "Py_NE"), ("__gt__", "Py_GT"), ("__ge__", "Py_GE"),
    )

    def _ancestor_compare_dispatcher(self, cls: dict, sym: str) -> str | None:
        """The richcompare dispatcher symbol of the nearest exposed ancestor
        that defines any comparison dunder, or None. A derived dispatcher
        delegates the ops it doesn't resolve itself there (tp_richcompare is
        one slot; per-op delegation restores Python's per-dunder MRO lookup)."""
        reg = self.ctx.analyzer.registry
        for anc in reg.iter_ancestor_records(cls["info"]):
            if any(m in anc.methods for m, _op in self._COMPARE_DUNDER_OPS):
                return f"{sym}__{escape_cpp_name(anc.name)}__richcompare_slot"
        return None

    def _ancestor_defines(self, info, name: str) -> bool:
        """True when any ancestor record defines `name` itself (own methods
        of the MRO tail; exposed hierarchies have all-user ancestors)."""
        reg = self.ctx.analyzer.registry
        return any(name in anc.methods for anc in reg.iter_ancestor_records(info))

    def _method_with_ancestors(self, info, name: str):
        """MRO-faithful single-method lookup for a combined-slot half: own
        first, then ancestors. The generated `payload.<name>(...)` call
        resolves the inherited C++ method the same way, so a derived slot can
        serve a half the class only inherits (a partial override would
        otherwise shadow the base's whole slot and lose the other half)."""
        overloads = self.ctx.analyzer.registry.get_method_overloads_with_parents(
            info, name)
        return overloads[0] if overloads else None

    def _emit_export_class_dunder_slots(self, out: TextIO, cls: dict, sym: str,
                                        reg_arg: str, cppvar: str
                                        ) -> list[tuple[str, str]]:
        """Emit repr/str/richcompare/hash wrapper functions for an exposed
        class's dunder methods and return the (slot-id, C++ expression) pairs
        to splice into the class's PyType_Slot table. A dunder the record
        doesn't define contributes no slot -- PyType_FromSpec then falls back
        to `object`'s own (identity hash, no richcompare), the same as any
        fresh heap type.
        """
        info = cls["info"]
        cpp = cls["cpp_type"]
        tv = cls["var"]
        base = f"{sym}__{escape_cpp_name(cls['simple'])}"
        slots: list[tuple[str, str]] = []

        for mname, slot_id in (("__repr__", "Py_tp_repr"), ("__str__", "Py_tp_str")):
            if mname not in info.methods:
                continue
            wname = f"{base}__{mname.strip('_')}_slot"
            slots.append((slot_id, wname))
            out.write(f"PyObject *{wname}(PyObject *self) {{\n")
            out.write("    try {\n")
            call = f"{cppvar}->payload.{mname}()"
            self._emit_call_return(out, info.methods[mname][0].return_type, call, sym)
            self._emit_boundary_catch(out, reg_arg)
            out.write("}\n\n")

        compare_defined = [m for m, _op in self._COMPARE_DUNDER_OPS
                           if m in info.methods]
        anc = self._ancestor_compare_dispatcher(cls, sym)
        if compare_defined:
            wname = f"{base}__richcompare_slot"
            slots.append(("Py_tp_richcompare", wname))
            out.write(f"PyObject *{wname}(PyObject *self, PyObject *other, "
                      f"int op) {{\n")
            # tp_richcompare is one slot for all six ops, so a derived class
            # defining SOME ops would otherwise shadow the base's dispatcher
            # for the rest. Route the ops this class doesn't resolve itself to
            # the nearest ancestor dispatcher up front -- before this class's
            # operand-type guard, so the ancestor applies its own (wider)
            # operand check, mirroring Python's per-dunder MRO lookup.
            # __ne__ is resolved here (as !__eq__) only when no ancestor
            # defines an explicit __ne__ -- Python's MRO would dispatch != to
            # that ancestor's real body, not to a negation of the (possibly
            # overridden) __eq__; object.__ne__'s auto-derivation applies only
            # when no class in the MRO defines __ne__ itself.
            ne_own = "__ne__" in info.methods or (
                "__eq__" in info.methods
                and not self._ancestor_defines(info, "__ne__"))
            if anc is not None:
                own_ops = [opconst for opname, opconst
                           in self._COMPARE_DUNDER_OPS
                           if opname in info.methods
                           or (opname == "__ne__" and ne_own)]
                out.write("    switch (op) {\n")
                out.write(f"    {' '.join(f'case {oc}:' for oc in own_ops)}\n")
                out.write("        break;\n")
                out.write("    default:\n")
                out.write(f"        return {anc}(self, other, op);\n")
                out.write("    }\n")
            out.write("    auto *__ot = Py_TYPE(other);\n")
            out.write(f"    if (__ot != (::tpy::cpy::PyTypeObject *){tv} && "
                      f"PyType_IsSubtype(__ot, (::tpy::cpy::PyTypeObject *){tv}) "
                      f"== 0)\n")
            out.write("        return ::tpy::interop::notimplemented_to_py();\n")
            out.write("    try {\n")
            out.write(f"        auto &__self = {cppvar}->payload;\n")
            out.write(f"        auto &__other = reinterpret_cast<"
                      f"::tpy::interop::Instance<{cpp}> *>(other)->payload;\n")
            out.write("        switch (op) {\n")
            for opname, opconst in self._COMPARE_DUNDER_OPS:
                out.write(f"        case {opconst}:\n")
                if opname in info.methods:
                    out.write(f"            return ::tpy::interop::to_py("
                              f"__self.{opname}(__other));\n")
                elif opname == "__ne__" and ne_own and "__eq__" in info.methods:
                    out.write("            return ::tpy::interop::to_py("
                              "!__self.__eq__(__other));\n")
                else:
                    out.write("            return "
                              "::tpy::interop::notimplemented_to_py();\n")
            out.write("        default:\n")
            out.write("            return ::tpy::interop::notimplemented_to_py();\n")
            out.write("        }\n")
            self._emit_boundary_catch(out, reg_arg)
            out.write("}\n\n")

        if "__hash__" in info.methods:
            wname = f"{base}__hash_slot"
            slots.append(("Py_tp_hash", wname))
            out.write(f"Py_ssize_t {wname}(PyObject *self) {{\n")
            out.write("    try {\n")
            out.write(f"        return ::tpy::interop::hash_to_py_hash_t("
                      f"static_cast<std::uint64_t>("
                      f"{cppvar}->payload.__hash__()));\n")
            out.write("    } catch (const ::tpy::BaseException &__e) {\n")
            out.write(f"        ::tpy::interop::set_py_err_from(__e{reg_arg});\n")
            out.write("        return -1;\n")
            out.write("    } catch (...) {\n")
            out.write("        if (!PyErr_Occurred())\n")
            out.write('            PyErr_SetString(PyExc_RuntimeError, '
                      '"tpy extension: __hash__ failed");\n')
            out.write("        return -1;\n")
            out.write("    }\n}\n\n")
            # PyType_Ready inherits tp_hash and tp_richcompare only as a PAIR
            # (both-NULL), so an own __hash__ with no own comparisons would
            # silently block the ancestor's comparisons from inheriting --
            # wire the ancestor dispatcher explicitly to keep them (its own
            # operand guard already admits subtype instances).
            if not compare_defined and anc is not None:
                slots.append(("Py_tp_richcompare", anc))
        elif compare_defined:
            # Any richcompare dunder without __hash__: the type becomes
            # unhashable. This mirrors PyType_Ready's OWN behavior for a
            # heap type built via PyType_FromSpec -- it nulls the hash
            # whenever tp_richcompare is populated at all, regardless of
            # which comparison op populated it (there is no per-dunder-name
            # introspection available at the C level, unlike a Python
            # class-statement's type_new, which specifically checks for an
            # `__eq__` key in the class dict). So this is NOT the same rule
            # as plain Python's -- a class overriding only __lt__ stays
            # hashable in plain Python but becomes unhashable once exposed
            # (verified empirically: PyObject_HashNotImplemented need not
            # even be wired here for the type to end up unhashable, since
            # PyType_Ready does it regardless of this slot table). Explicit
            # wiring here is for clarity/consistency, not because it's what
            # causes the unhashability. Acknowledged, unavoidable divergence
            # -- see docs/CPYTHON_INTEROP.md.
            slots.append(("Py_tp_hash", "PyObject_HashNotImplemented"))

        return slots

    def _value_in_decl_expr(self, typ: TpyType, src: str, sym: str) -> tuple[str, str]:
        """The local's C++ declaration type and marshal expression for a single
        incoming value (scalar/exposed-class/exposed-enum -- containers/Span
        don't reach here). Shared by an operator dunder's non-self operand and a
        getset field setter. An exposed-class value binds a const reference to
        the live embedded payload (mirrors the record's own `const Cls&` param,
        no copy); a scalar/enum value is a fresh owned value, like a normal
        @export function param. (For a getset field setter the exposed-class arm
        is reached only by an immutable value-type field, where the copy-in is
        sound; a mutable reference-class field is sema-rejected.)"""
        if is_exposed_class(typ):
            cpp, tv = self._class_cpp_var(typ, sym)
            return (f"const {cpp} &",
                    f"*::tpy::interop::instance_payload<{cpp}>("
                    f"{src}, (::tpy::cpy::PyTypeObject *){tv})")
        if is_exposed_enum(typ):
            cpp, ev = self._enum_cpp_var(typ, sym)
            return f"{cpp} ", f"::tpy::interop::enum_from_py<{cpp}>({src}, {ev})"
        cpp = boundary_cpp_type(_boundary_inner(typ))
        return f"{cpp} ", f"::tpy::interop::from_py<{cpp}>({src})"

    def _value_out_expr(self, call_expr: str, ret_typ: TpyType | None, sym: str) -> str:
        """The C++ expression producing the PyObject* for a single outgoing
        value -- an expression-form mirror of `_emit_call_return`'s dispatch,
        used where the caller needs a bare `return <expr>;` rather than a
        multi-line statement emitter: an operator-dunder result (its
        forward/reflected branches already sit inside their own try) and a
        getset field getter. (For a field getter the exposed-class arm is
        reached only by an immutable value-type field, which copies out; a
        mutable reference-class field is sema-rejected.)"""
        if ret_typ is not None and is_exposed_class(ret_typ):
            _cpp, tv = self._class_cpp_var(ret_typ, sym)
            return (f"::tpy::interop::instance_to_py("
                    f"(::tpy::cpy::PyTypeObject *){tv}, {call_expr})")
        if ret_typ is not None and is_exposed_enum(ret_typ):
            _cpp, ev = self._enum_cpp_var(ret_typ, sym)
            und = enum_info_of(_boundary_inner(ret_typ)).underlying_type.to_cpp()
            return f"::tpy::interop::enum_to_py({ev}, static_cast<{und}>({call_expr}))"
        if _container_element_types(_boundary_inner(ret_typ)) is not None:
            return self._marshal_out_expr(ret_typ, call_expr, 0)
        return f"::tpy::interop::to_py({call_expr})"

    def _emit_export_class_binary_op(self, out: TextIO, cls: dict, sym: str,
                                     reg_arg: str, dunder: str, slot_id: str,
                                     reflected: str | None) -> tuple[str, str] | None:
        """Emit one nb_* wrapper for a binary arithmetic dunder (forward
        `dunder`, e.g. `__add__`, plus its `reflected` counterpart, e.g.
        `__radd__`, if the record defines it) -- `__pow__`/`__rpow__` included
        (CPython's nb_power is ternary; the wrapper rejects a non-None `mod`,
        matching a type that doesn't support 3-arg `pow`). Returns None if the
        record defines neither dunder.

        `a op b`: CPython invokes this wrapper whenever EITHER operand's type
        registers this slot, with the SAME (a, b) order regardless of which
        side is `self` -- so the wrapper checks a's type for the forward call
        and b's type for the reflected one, downgrading a wrong-typed operand
        (a TypeError from the marshaller) to NotImplemented rather than
        raising, so CPython can fall through to the other side / the standard
        "unsupported operand type(s)" error.
        """
        info = cls["info"]
        cpp = cls["cpp_type"]
        tv = cls["var"]
        # Emit only when the class ITSELF defines a half (full inheritance
        # keeps the base's slot); each emitted half then resolves
        # MRO-faithfully, so a partial override still serves the inherited
        # other half instead of shadowing it behind NotImplemented.
        if dunder not in info.methods and (
                reflected is None or reflected not in info.methods):
            return None
        fwd_m = self._method_with_ancestors(info, dunder)
        rev_m = (self._method_with_ancestors(info, reflected)
                 if reflected is not None else None)
        base = f"{sym}__{escape_cpp_name(cls['simple'])}"
        wname = f"{base}__{dunder.strip('_')}_slot"
        is_pow = dunder == "__pow__"
        sig = (f"PyObject *{wname}(PyObject *a, PyObject *b, PyObject *mod)"
               if is_pow else f"PyObject *{wname}(PyObject *a, PyObject *b)")
        out.write(f"{sig} {{\n")
        if is_pow:
            # 3-arg pow(a, b, mod): reject a real modulus (not supported),
            # matching CPython's own NotImplemented convention for a type
            # whose nb_power doesn't implement the ternary form.
            out.write("    if (mod != &_Py_NoneStruct)\n")
            out.write("        return ::tpy::interop::notimplemented_to_py();\n")
        out.write("    try {\n")

        def branch(operand_var: str, self_var: str, meth: str, m) -> None:
            operand_type = m.params[0].type
            ret_typ = m.return_type
            out.write(f"        if (Py_TYPE({self_var}) == "
                      f"(::tpy::cpy::PyTypeObject *){tv} || PyType_IsSubtype("
                      f"Py_TYPE({self_var}), (::tpy::cpy::PyTypeObject *){tv}"
                      f") != 0) {{\n")
            out.write("            try {\n")
            decl, expr = self._value_in_decl_expr(operand_type, operand_var, sym)
            out.write(f"                {decl}__other = {expr};\n")
            call = (f"reinterpret_cast<::tpy::interop::Instance<{cpp}> *>"
                    f"({self_var})->payload.{meth}(__other)")
            out.write(f"                return {self._value_out_expr(call, ret_typ, sym)};\n")
            out.write("            } catch (const ::tpy::interop::MarshalError &) {\n")
            out.write("                if (!PyErr_ExceptionMatches(PyExc_TypeError)) "
                      "return nullptr;\n")
            out.write("                PyErr_Clear();\n")
            out.write("            }\n")
            out.write("        }\n")

        if fwd_m is not None:
            branch("b", "a", dunder, fwd_m)
        if rev_m is not None:
            branch("a", "b", reflected, rev_m)
        out.write("        return ::tpy::interop::notimplemented_to_py();\n")
        self._emit_boundary_catch(out, reg_arg)
        out.write("}\n\n")
        return slot_id, wname

    def _emit_export_class_unary_op(self, out: TextIO, cls: dict, sym: str,
                                    reg_arg: str, cppvar: str, dunder: str,
                                    slot_id: str) -> tuple[str, str] | None:
        """Emit one nb_* wrapper for a unary arithmetic dunder (__pos__/
        __neg__/__invert__); reuses the same call/return shape as repr/str
        (unaryfunc, no operand to marshal). Returns None if undefined."""
        info = cls["info"]
        if dunder not in info.methods:
            return None
        base = f"{sym}__{escape_cpp_name(cls['simple'])}"
        wname = f"{base}__{dunder.strip('_')}_slot"
        out.write(f"PyObject *{wname}(PyObject *self) {{\n")
        out.write("    try {\n")
        call = f"{cppvar}->payload.{dunder}()"
        out.write(f"        return {self._value_out_expr(call, info.methods[dunder][0].return_type, sym)};\n")
        self._emit_boundary_catch(out, reg_arg)
        out.write("}\n\n")
        return slot_id, wname

    def _emit_export_class_inplace_op(self, out: TextIO, cls: dict, sym: str,
                                      reg_arg: str, cppvar: str, dunder: str,
                                      slot_id: str) -> tuple[str, str] | None:
        """Emit one nb_inplace_* wrapper for an in-place arithmetic dunder
        (__iadd__/...). TPy already requires these to mutate self and return
        self (`CONST_PARAMS_METHODS` in typesys.py rejects any other return
        shape at registration, export or not), so the wrapper always hands
        back the same `self` (a fresh reference) rather than marshalling a
        return value. A wrong-typed operand downgrades to NotImplemented,
        like the forward/reflected binary case."""
        info = cls["info"]
        if dunder not in info.methods:
            return None
        base = f"{sym}__{escape_cpp_name(cls['simple'])}"
        wname = f"{base}__{dunder.strip('_')}_slot"
        operand_type = info.methods[dunder][0].params[0].type
        out.write(f"PyObject *{wname}(PyObject *self, PyObject *other) {{\n")
        out.write("    try {\n")
        decl, expr = self._value_in_decl_expr(operand_type, "other", sym)
        out.write(f"        {decl}__other = {expr};\n")
        out.write(f"        {cppvar}->payload.{dunder}(__other);\n")
        out.write("        Py_IncRef(self);\n")
        out.write("        return self;\n")
        out.write("    } catch (const ::tpy::interop::MarshalError &) {\n")
        out.write("        if (!PyErr_ExceptionMatches(PyExc_TypeError)) return nullptr;\n")
        out.write("        PyErr_Clear();\n")
        out.write("        return ::tpy::interop::notimplemented_to_py();\n")
        self._emit_boundary_catch(out, reg_arg)
        out.write("}\n\n")
        return slot_id, wname

    def _emit_export_class_operator_slots(self, out: TextIO, cls: dict, sym: str,
                                          reg_arg: str, cppvar: str
                                          ) -> list[tuple[str, str]]:
        """Emit the arithmetic/ordering operator group: binary (forward +
        reflected), unary, and in-place dunders -> Py_nb_* slots. Returns the
        (slot-id, C++ expression) pairs to splice into the
        class's PyType_Slot table."""
        slots: list[tuple[str, str]] = []
        for dunder, (slot_id, reflected) in _NB_BINARY_OPS.items():
            r = self._emit_export_class_binary_op(
                out, cls, sym, reg_arg, dunder, slot_id, reflected)
            if r is not None:
                slots.append(r)
        for dunder, slot_id in _NB_UNARY_OPS.items():
            r = self._emit_export_class_unary_op(
                out, cls, sym, reg_arg, cppvar, dunder, slot_id)
            if r is not None:
                slots.append(r)
        for dunder, slot_id in _NB_INPLACE_OPS.items():
            r = self._emit_export_class_inplace_op(
                out, cls, sym, reg_arg, cppvar, dunder, slot_id)
            if r is not None:
                slots.append(r)
        return slots

    def _emit_export_class_len(self, out: TextIO, cls: dict, sym: str,
                               reg_arg: str, cppvar: str) -> list[tuple[str, str]]:
        """Emit the __len__ wrapper (lenfunc), wired to BOTH Py_mp_length and
        Py_sq_length (one wrapper, two slot entries) -- matching how CPython
        wires a plain `class C: def __len__(self): ...` (`len(x)` prefers
        mp_length but sq_length backs older sequence-protocol call sites)."""
        info = cls["info"]
        if "__len__" not in info.methods:
            return []
        base = f"{sym}__{escape_cpp_name(cls['simple'])}"
        wname = f"{base}__len_slot"
        out.write(f"Py_ssize_t {wname}(PyObject *self) {{\n")
        out.write("    try {\n")
        out.write(f"        return static_cast<Py_ssize_t>("
                  f"{cppvar}->payload.__len__());\n")
        out.write("    } catch (const ::tpy::BaseException &__e) {\n")
        out.write(f"        ::tpy::interop::set_py_err_from(__e{reg_arg});\n")
        out.write("        return -1;\n")
        out.write("    } catch (...) {\n")
        out.write("        if (!PyErr_Occurred())\n")
        out.write('            PyErr_SetString(PyExc_RuntimeError, '
                  '"tpy extension: __len__ failed");\n')
        out.write("        return -1;\n")
        out.write("    }\n}\n\n")
        return [("Py_mp_length", wname), ("Py_sq_length", wname)]

    def _emit_export_class_getitem(self, out: TextIO, cls: dict, sym: str,
                                   reg_arg: str, cppvar: str
                                   ) -> tuple[str, str] | None:
        """Emit __getitem__ -> Py_mp_subscript (binaryfunc: self, key). No
        NotImplemented downgrade on a wrong-typed key -- unlike an arithmetic
        operator, subscripting has no reflected/fallback side, so the
        marshaller's TypeError (already set) just propagates through the
        generic catch. `__getitem__(self, s: slice)` (slicing) is out of
        scope; the key marshals as whatever single type the dunder declares."""
        info = cls["info"]
        if "__getitem__" not in info.methods:
            return None
        m = info.methods["__getitem__"][0]
        key_type = m.params[0].type
        base = f"{sym}__{escape_cpp_name(cls['simple'])}"
        wname = f"{base}__getitem_slot"
        out.write(f"PyObject *{wname}(PyObject *self, PyObject *key) {{\n")
        out.write("    try {\n")
        decl, expr = self._value_in_decl_expr(key_type, "key", sym)
        out.write(f"        {decl}__key = {expr};\n")
        call = f"{cppvar}->payload.__getitem__(__key)"
        out.write(f"        return {self._value_out_expr(call, m.return_type, sym)};\n")
        self._emit_boundary_catch(out, reg_arg)
        out.write("}\n\n")
        return "Py_mp_subscript", wname

    def _emit_export_class_ass_subscript(self, out: TextIO, cls: dict, sym: str,
                                         reg_arg: str, cppvar: str
                                         ) -> tuple[str, str] | None:
        """Emit __setitem__/__delitem__ -> ONE Py_mp_ass_subscript wrapper
        (objobjargproc: self, key, value); CPython calls this with value ==
        nullptr for `del obj[k]`. Emitted whenever either dunder is defined
        by the class ITSELF (a class inheriting both keeps the base's slot);
        each half then resolves MRO-faithfully, so a partial override still
        serves the inherited other half instead of shadowing it. A half
        defined nowhere in the hierarchy raises the same TypeError CPython
        itself gives a type without it."""
        info = cls["info"]
        if ("__setitem__" not in info.methods
                and "__delitem__" not in info.methods):
            return None
        set_m = self._method_with_ancestors(info, "__setitem__")
        del_m = self._method_with_ancestors(info, "__delitem__")
        base = f"{sym}__{escape_cpp_name(cls['simple'])}"
        wname = f"{base}__ass_subscript_slot"
        out.write(f"int {wname}(PyObject *self, PyObject *key, PyObject *value) {{\n")
        out.write("    try {\n")
        out.write("        if (value == nullptr) {\n")
        if del_m is not None:
            key_type = del_m.params[0].type
            decl, expr = self._value_in_decl_expr(key_type, "key", sym)
            out.write(f"            {decl}__key = {expr};\n")
            out.write(f"            {cppvar}->payload.__delitem__(__key);\n")
            out.write("            return 0;\n")
        else:
            out.write('            PyErr_SetString(PyExc_TypeError, '
                      '"object doesn\'t support item deletion");\n')
            out.write("            return -1;\n")
        out.write("        }\n")
        if set_m is not None:
            key_type, val_type = (p.type for p in set_m.params[:2])
            kdecl, kexpr = self._value_in_decl_expr(key_type, "key", sym)
            vdecl, vexpr = self._value_in_decl_expr(val_type, "value", sym)
            out.write(f"        {kdecl}__key = {kexpr};\n")
            out.write(f"        {vdecl}__value = {vexpr};\n")
            out.write(f"        {cppvar}->payload.__setitem__(__key, __value);\n")
            out.write("        return 0;\n")
        else:
            out.write('        PyErr_SetString(PyExc_TypeError, '
                      '"object does not support item assignment");\n')
            out.write("        return -1;\n")
        out.write("    } catch (const ::tpy::BaseException &__e) {\n")
        out.write(f"        ::tpy::interop::set_py_err_from(__e{reg_arg});\n")
        out.write("        return -1;\n")
        out.write("    } catch (...) {\n")
        out.write("        if (!PyErr_Occurred())\n")
        out.write('            PyErr_SetString(PyExc_RuntimeError, '
                  '"tpy extension: item assignment failed");\n')
        out.write("        return -1;\n")
        out.write("    }\n}\n\n")
        return "Py_mp_ass_subscript", wname

    def _emit_export_class_contains(self, out: TextIO, cls: dict, sym: str,
                                    reg_arg: str, cppvar: str
                                    ) -> tuple[str, str] | None:
        """Emit __contains__ -> Py_sq_contains (objobjproc: self, value ->
        -1/0/1). Omitted entirely when undefined -- `in` then falls back to
        CPython's own iterate-via-tp_iter behavior (PySequence_Contains),
        free once __iter__/__next__ are wired, so no explicit fallback code
        is needed here."""
        info = cls["info"]
        if "__contains__" not in info.methods:
            return None
        value_type = info.methods["__contains__"][0].params[0].type
        base = f"{sym}__{escape_cpp_name(cls['simple'])}"
        wname = f"{base}__contains_slot"
        out.write(f"int {wname}(PyObject *self, PyObject *value) {{\n")
        out.write("    try {\n")
        decl, expr = self._value_in_decl_expr(value_type, "value", sym)
        out.write(f"        {decl}__v = {expr};\n")
        out.write(f"        return {cppvar}->payload.__contains__(__v) ? 1 : 0;\n")
        out.write("    } catch (const ::tpy::BaseException &__e) {\n")
        out.write(f"        ::tpy::interop::set_py_err_from(__e{reg_arg});\n")
        out.write("        return -1;\n")
        out.write("    } catch (...) {\n")
        out.write("        if (!PyErr_Occurred())\n")
        out.write('            PyErr_SetString(PyExc_RuntimeError, '
                  '"tpy extension: __contains__ failed");\n')
        out.write("        return -1;\n")
        out.write("    }\n}\n\n")
        return "Py_sq_contains", wname

    def _emit_export_class_next(self, out: TextIO, cls: dict, sym: str,
                                reg_arg: str, cppvar: str
                                ) -> tuple[str, str] | None:
        """Emit __next__ -> Py_tp_iternext (iternextfunc/unaryfunc shape).
        `__next__` is implicitly `@error_return(StopIteration)` (the parser
        default), so the compiled method returns `std::expected<T,
        StopIteration>` rather than throwing on exhaustion -- unwrap it
        directly (`set_py_err_from` on the StopIteration value, no try/catch
        needed for that path) rather than reusing `_emit_boundary_catch`'s
        catch-a-thrown-exception shape, which does not apply here."""
        info = cls["info"]
        if "__next__" not in info.methods:
            return None
        ret_typ = info.methods["__next__"][0].return_type
        base = f"{sym}__{escape_cpp_name(cls['simple'])}"
        wname = f"{base}__next_slot"
        out.write(f"PyObject *{wname}(PyObject *self) {{\n")
        out.write("    try {\n")
        out.write(f"        auto __r = {cppvar}->payload.__next__();\n")
        out.write("        if (!__r.has_value()) {\n")
        out.write(f"            ::tpy::interop::set_py_err_from(__r.error(){reg_arg});\n")
        out.write("            return nullptr;\n")
        out.write("        }\n")
        call = "std::move(__r).value()"
        out.write(f"        return {self._value_out_expr(call, ret_typ, sym)};\n")
        self._emit_boundary_catch(out, reg_arg)
        out.write("}\n\n")
        return "Py_tp_iternext", wname

    def _emit_export_class_iter(self, out: TextIO, cls: dict, sym: str,
                                reg_arg: str, cppvar: str
                                ) -> tuple[str, str] | None:
        """Emit __iter__ -> Py_tp_iter (unaryfunc shape). Unlike the
        arithmetic unary ops this goes through `_emit_call_return` with the
        receiver as an alias candidate: the canonical `return self` iterator
        crosses as the SAME PyObject -- a copied iterator would be silently
        restartable and interleaved next(obj) would diverge. An Own[...]
        return (a fresh iterator object) takes the usual fresh-instance path."""
        info = cls["info"]
        if "__iter__" not in info.methods:
            return None
        base = f"{sym}__{escape_cpp_name(cls['simple'])}"
        wname = f"{base}__iter_slot"
        out.write(f"PyObject *{wname}(PyObject *self) {{\n")
        out.write("    try {\n")
        out.write(f"        auto &__self = {cppvar}->payload;\n")
        self._emit_call_return(out, info.methods["__iter__"][0].return_type,
                               "__self.__iter__()", sym,
                               [("__self", "self", info)])
        self._emit_boundary_catch(out, reg_arg)
        out.write("}\n\n")
        return "Py_tp_iter", wname

    def _emit_export_class_container_slots(self, out: TextIO, cls: dict, sym: str,
                                           reg_arg: str, cppvar: str
                                           ) -> list[tuple[str, str]]:
        """Emit the container-protocol group: __len__, __getitem__,
        __setitem__/__delitem__, __contains__, __iter__, __next__ ->
        Py_mp_*/Py_sq_*/Py_tp_iter*. Returns the (slot-id, C++ expression)
        pairs to splice into the class's PyType_Slot table."""
        slots: list[tuple[str, str]] = []
        slots += self._emit_export_class_len(out, cls, sym, reg_arg, cppvar)
        for fn in (self._emit_export_class_getitem,
                  self._emit_export_class_ass_subscript,
                  self._emit_export_class_contains,
                  self._emit_export_class_next,
                  self._emit_export_class_iter):
            r = fn(out, cls, sym, reg_arg, cppvar)
            if r is not None:
                slots.append(r)
        return slots

    def _emit_exposed_class(self, out: TextIO, cls: dict, sym: str,
                            reg_arg: str) -> None:
        """Emit one exposed class's method/getset wrappers, the slot tables, and
        the PyType_Spec. The type is created at PyInit_ (see generate_extension_
        glue); instances embed the TPy payload after the PyObject header
        (tpy::interop::Instance<T>)."""
        info = cls["info"]
        cpp = cls["cpp_type"]
        cppvar = f"reinterpret_cast<::tpy::interop::Instance<{cpp}> *>(self)"

        # tp_init: marshal __init__ args, then (destroy any prior payload and)
        # placement-new the embedded one. tp_init can run more than once (an
        # explicit `obj.__init__(...)`); the `initialized` flag drives the
        # destroy-before-reinit so a re-init doesn't overwrite a live payload
        # (which would leak its non-trivial fields). Args are marshalled before
        # the destroy, so a marshalling failure leaves the existing payload
        # intact. `initialized` is cleared across the rebuild so a throwing
        # constructor can't leave tp_dealloc to double-destroy.
        if "__init__" in info.methods:
            init_params = [(p.name, p.type)
                           for p in info.methods["__init__"][0].params
                           if p.name != "self"]
        elif info.inherits_init_from is not None:
            # Ctor inheritance: no own __init__, so the signature comes from
            # the registration-copied init_params (the C++ record inherits the
            # ctor via `using Base::Base`, so the placement-new call matches).
            init_params = [(pn, pt) for pn, pt, _default in info.init_params]
        else:
            init_params = []
        n_init = len(init_params)
        init_fn = f"{sym}__{escape_cpp_name(cls['simple'])}_init"
        kw_param = "kwargs" if n_init else ""
        out.write(f"int {init_fn}(PyObject *self, PyObject *args, "
                  f"PyObject *{kw_param}) {{\n")
        if cls["basetype"]:
            # BASETYPE (required to be another exposed class's tp_base) also
            # legalizes a Python-side `class Mine(mymod.Cls)` statement; its
            # inherited tp_init would placement-new a payload the C++ side
            # never dispatches to Python overrides on, so reject the subclass
            # loudly at instantiation. Exposed TPy subclasses never enter:
            # each has its own tp_init guarded against its own type.
            out.write(f"    if (Py_TYPE(self) != "
                      f"(::tpy::cpy::PyTypeObject *){cls['var']}) {{\n")
            out.write(f'        PyErr_SetString(PyExc_TypeError, '
                      f'"Python-defined subclasses of exposed class '
                      f'\'{cls["py_name"]}\' are not supported");\n')
            out.write("        return -1;\n    }\n")
        if n_init:
            self._emit_arg_unpack(out, [pn for pn, _t in init_params], "-1",
                                  cls['simple'])
        out.write(f"    auto *__inst = {cppvar};\n")
        out.write("    try {\n")
        argtoks = [self._emit_marshal_in(out, i, t, sym)
                   for i, (_pn, t) in enumerate(init_params)]
        out.write("        if (__inst->initialized) { __inst->initialized = "
                  "false; ::std::destroy_at(&__inst->payload); }\n")
        out.write(f"        new (&__inst->payload) {cpp}({', '.join(argtoks)});\n")
        out.write("        __inst->initialized = true;\n")
        out.write("        return 0;\n")
        # __init__ failure returns the -1 init sentinel, not the NULL wrapper
        # sentinel -- so a localized catch (not _emit_boundary_catch).
        out.write("    } catch (const ::tpy::BaseException &__e) {\n")
        out.write(f"        ::tpy::interop::set_py_err_from(__e{reg_arg});\n")
        out.write("        return -1;\n")
        out.write("    } catch (...) {\n")
        out.write("        if (!PyErr_Occurred())\n")
        out.write('            PyErr_SetString(PyExc_RuntimeError, '
                  '"tpy extension: constructor failed");\n')
        out.write("        return -1;\n")
        out.write("    }\n")
        out.write("}\n\n")

        # Instance methods (plain, non-dunder; the sema validator guaranteed the
        # signatures marshal and rejected static/async/generator/generic).
        method_entries: list[tuple[str, str, str, bool]] = []
        for mname, overloads in info.methods.items():
            if mname == "__init__":
                continue
            if mname.startswith("__") and mname.endswith("__"):
                continue
            m = overloads[0]
            params = [(p.name, p.type) for p in m.params if p.name != "self"]
            n = len(params)
            wname = f"{sym}__{escape_cpp_name(cls['simple'])}__" \
                    f"{escape_cpp_name(mname)}_pywrap"
            if n == 0:
                meth_flag, kw = "METH_NOARGS", False
                out.write(f"PyObject *{wname}(PyObject *self, PyObject *) {{\n")
            else:
                meth_flag, kw = "METH_VARARGS | METH_KEYWORDS", True
                out.write(f"PyObject *{wname}(PyObject *self, PyObject *args, "
                          f"PyObject *kwargs) {{\n")
                self._emit_arg_unpack(out, [pn for pn, _t in params], "nullptr",
                                      mname)
            method_entries.append((mname, wname, meth_flag, kw))
            out.write("    try {\n")
            out.write(f"        auto &__self = {cppvar}->payload;\n")
            argtoks = [self._emit_marshal_in(out, i, t, sym)
                       for i, (_pn, t) in enumerate(params)]
            call = f"__self.{escape_cpp_name(mname)}({', '.join(argtoks)})"
            self._emit_call_return(
                out, m.return_type, call, sym,
                [("__self", "self", info)]
                + self._param_alias_candidates(params))
            self._emit_boundary_catch(out, reg_arg)
            out.write("}\n\n")

        # getset: every crossing field as a descriptor. `_`-named fields are
        # internal payload state and skipped here (never a Python attribute).
        # Crossing field types are scalar/str/bytes, an exposed enum, or (on a
        # reference class) an exposed VALUE-type field; a public reference-class
        # field stays sema-rejected (its inline copy-out silently breaks
        # write-through; make it internal with a `_` name to hold it). The get/set reuse the
        # same single-value marshal helpers the operator dunders use, so an enum
        # or value-class field round-trips its value while a scalar field's
        # getset still emits a plain `to_py`/`from_py` (the helpers' scalar arm).
        # A VALUE-type record is
        # immutable, so its own fields are exposed READ-ONLY (nullptr setter) --
        # matching the language rule that its fields are set only in __init__.
        read_only = info.is_value_type
        getset_entries: list[tuple[str, str, str]] = []
        for fld in info.fields:
            if is_internal_boundary_field(fld.name):
                continue  # `_`-named field stays payload-only, never a getset
            fcpp = escape_cpp_name(fld.name)
            getn = f"{sym}__{escape_cpp_name(cls['simple'])}__{fcpp}_get"
            setn = f"{sym}__{escape_cpp_name(cls['simple'])}__{fcpp}_set"
            getset_entries.append((fld.name, getn, "nullptr" if read_only else setn))
            out.write(f"PyObject *{getn}(PyObject *self, void *) {{\n")
            out.write("    try {\n")
            out.write(f"        return {self._value_out_expr(f'{cppvar}->payload.{fcpp}', fld.type, sym)};\n")
            out.write("    } catch (...) {\n")
            out.write("        if (!PyErr_Occurred())\n")
            out.write('            PyErr_SetString(PyExc_RuntimeError, '
                      '"tpy extension: attribute read failed");\n')
            out.write("        return nullptr;\n")
            out.write("    }\n}\n")
            if read_only:
                continue
            _, fin = self._value_in_decl_expr(fld.type, "value", sym)
            out.write(f"int {setn}(PyObject *self, PyObject *value, void *) {{\n")
            # `del obj.attr` invokes the setter with value == NULL (the getset
            # delete protocol) -- guard before the marshaller dereferences it.
            out.write("    if (value == nullptr) {\n")
            out.write(f'        PyErr_SetString(PyExc_AttributeError, '
                      f'"attribute \'{fld.name}\' cannot be deleted");\n')
            out.write("        return -1;\n    }\n")
            out.write("    try {\n")
            out.write(f"        {cppvar}->payload.{fcpp} = {fin};\n")
            out.write("        return 0;\n")
            out.write("    } catch (...) {\n")
            out.write("        if (!PyErr_Occurred())\n")
            out.write('            PyErr_SetString(PyExc_RuntimeError, '
                      '"tpy extension: attribute write failed");\n')
            out.write("        return -1;\n")
            out.write("    }\n}\n\n")

        # @property accessors as computed getset. The getter body is the
        # zero-arg method emit (same _emit_call_return, so the return admits
        # the full method boundary set incl. containers); the setter body is
        # the one-param method emit, its single value fed to _emit_marshal_in
        # through an `a0` alias in place of the arg-unpack local. A getter
        # raising a TPy exception crosses like a method raise (boundary catch);
        # the setter mirrors tp_init's -1-sentinel catch. `_`-named properties
        # are internal like `_`-named fields. A getter-only property gets a
        # nullptr setter (AttributeError on write, as in CPython -- message
        # text differs).
        for pname, prop in info.properties.items():
            if is_internal_boundary_field(pname):
                continue
            pcpp = escape_cpp_name(pname)
            getn = f"{sym}__{escape_cpp_name(cls['simple'])}__{pcpp}_get"
            setn = f"{sym}__{escape_cpp_name(cls['simple'])}__{pcpp}_set"
            has_setter = prop.setter is not None
            getset_entries.append((pname, getn, setn if has_setter
                                   else "nullptr"))
            out.write(f"PyObject *{getn}(PyObject *self, void *) {{\n")
            out.write("    try {\n")
            out.write(f"        auto &__self = {cppvar}->payload;\n")
            self._emit_call_return(out, prop.getter.return_type,
                                   f"__self.{pcpp}()", sym,
                                   [("__self", "self", info)])
            self._emit_boundary_catch(out, reg_arg)
            out.write("}\n\n")
            if not has_setter:
                continue
            sp_type = next(p.type for p in prop.setter.params
                           if p.name != "self")
            out.write(f"int {setn}(PyObject *self, PyObject *value, "
                      f"void *) {{\n")
            # `del obj.prop` invokes the setter with value == NULL (the getset
            # delete protocol) -- AttributeError like CPython's deleter-less
            # property, instead of the marshaller dereferencing NULL.
            out.write("    if (value == nullptr) {\n")
            out.write(f'        PyErr_SetString(PyExc_AttributeError, '
                      f'"property \'{pname}\' has no deleter");\n')
            out.write("        return -1;\n    }\n")
            out.write("    PyObject *a0 = value;\n")
            out.write("    try {\n")
            out.write(f"        auto &__self = {cppvar}->payload;\n")
            tok = self._emit_marshal_in(out, 0, sp_type, sym)
            # A non-value setter param is an ownership transfer (Own
            # auto-wrap, C++ T&&) -- move the marshalled owned local in.
            if isinstance(sp_type, OwnType):
                tok = f"::std::move({tok})"
            out.write(f"        __self.set_{pcpp}({tok});\n")
            out.write("        return 0;\n")
            out.write("    } catch (const ::tpy::BaseException &__e) {\n")
            out.write(f"        ::tpy::interop::set_py_err_from(__e{reg_arg});\n")
            out.write("        return -1;\n")
            out.write("    } catch (...) {\n")
            out.write("        if (!PyErr_Occurred())\n")
            out.write('            PyErr_SetString(PyExc_RuntimeError, '
                      '"tpy extension: attribute write failed");\n')
            out.write("        return -1;\n")
            out.write("    }\n}\n\n")

        dunder_slots = self._emit_export_class_dunder_slots(
            out, cls, sym, reg_arg, cppvar)
        dunder_slots += self._emit_export_class_operator_slots(
            out, cls, sym, reg_arg, cppvar)
        dunder_slots += self._emit_export_class_container_slots(
            out, cls, sym, reg_arg, cppvar)

        base = f"{sym}__{escape_cpp_name(cls['simple'])}"
        out.write(f"PyMethodDef {base}__methods[] = {{\n")
        for pyname, wname, flag, kw in method_entries:
            slot = f"as_pycfunction({wname})" if kw else wname
            out.write(f'    {{"{pyname}", {slot}, {flag}, nullptr}},\n')
        out.write("    {nullptr, nullptr, 0, nullptr},\n};\n")
        out.write(f"PyGetSetDef {base}__getset[] = {{\n")
        for pyname, getn, setn in getset_entries:
            out.write(f'    {{"{pyname}", {getn}, {setn}, nullptr, nullptr}},\n')
        out.write("    {nullptr, nullptr, nullptr, nullptr, nullptr},\n};\n")
        out.write(f"PyType_Slot {base}__slots[] = {{\n")
        out.write(f"    {{Py_tp_init, (void *){init_fn}}},\n")
        out.write(f"    {{Py_tp_dealloc, "
                  f"(void *)::tpy::interop::instance_dealloc<{cpp}>}},\n")
        out.write(f"    {{Py_tp_methods, (void *){base}__methods}},\n")
        out.write(f"    {{Py_tp_getset, (void *){base}__getset}},\n")
        for slot_id, expr in dunder_slots:
            out.write(f"    {{{slot_id}, (void *){expr}}},\n")
        out.write("    {Py_tp_new, (void *)::tpy::cpy::PyType_GenericNew},\n")
        out.write("    {0, nullptr},\n};\n")
        flags = "Py_TPFLAGS_DEFAULT"
        if cls["basetype"]:
            flags += " | Py_TPFLAGS_BASETYPE"
        out.write(f"PyType_Spec {base}__spec = {{\n")
        out.write(f'    "{cls["py_name"]}", '
                  f"(int)sizeof(::tpy::interop::Instance<{cpp}>), 0,\n")
        out.write(f"    {flags}, {base}__slots,\n}};\n\n")

    def _emit_enum_create(self, out: TextIO, e: dict, module_name: str) -> None:
        """Emit the PyInit_ block that builds one @export enum's value dict and
        recreates it as a CPython IntEnum/Enum (make_enum), then adds it to the
        module and retains the module-static handle for the .so's lifetime (the
        value marshallers reference it). Runs with `__m` live (inside the try)."""
        und = e["underlying"]
        adds = " ||\n            ".join(
            f'::tpy::interop::enum_dict_add(__d, "{name}", '
            f"::tpy::interop::to_py(static_cast<{und}>({value}))) < 0"
            for name, value in e["members"])
        out.write("        {\n")
        out.write("            PyObject *__d = ::tpy::cpy::PyDict_New();\n")
        out.write("            if (!__d) { ::tpy::cpy::Py_DecRef(__m); "
                  "return nullptr; }\n")
        out.write(f"            if ({adds}) {{\n")
        out.write("                ::tpy::cpy::Py_DecRef(__d); "
                  "::tpy::cpy::Py_DecRef(__m); return nullptr;\n            }\n")
        out.write(f'            {e["var"]} = ::tpy::interop::make_enum('
                  f'"{e["simple"]}", "{module_name}", '
                  f'{"true" if e["is_int_enum"] else "false"}, __d);\n')
        out.write(f"            ::tpy::cpy::Py_DecRef(__d);\n")
        out.write(f'            if (!{e["var"]}) {{ ::tpy::cpy::Py_DecRef(__m); '
                  f"return nullptr; }}\n")
        # On success the module-static handle keeps its make_enum reference alive
        # (like the exposed-class type handles) and AddObjectRef adds the module
        # dict's own; on failure AddObjectRef took no ref, so release the handle's
        # before bailing (else a repeated failed init strands it).
        out.write(f'            if (::tpy::cpy::PyModule_AddObjectRef(__m, '
                  f'"{e["simple"]}", {e["var"]}) < 0) {{ '
                  f'::tpy::cpy::Py_DecRef({e["var"]}); '
                  f"::tpy::cpy::Py_DecRef(__m); return nullptr; }}\n")
        out.write("        }\n")

    def _emit_constant_add(self, out: TextIO, c: dict) -> None:
        """Emit the PyInit_ block that marshals one Final constant's value (read
        AFTER __tpy_init) and adds it to the module dict. Runs with `__m` live."""
        out.write("        {\n")
        out.write(f'            PyObject *__c = ::tpy::interop::to_py({c["cpp_ref"]});\n')
        out.write("            if (!__c) { ::tpy::cpy::Py_DecRef(__m); "
                  "return nullptr; }\n")
        out.write(f'            if (::tpy::cpy::PyModule_AddObjectRef(__m, '
                  f'"{c["py_name"]}", __c) < 0) {{ ::tpy::cpy::Py_DecRef(__c); '
                  f"::tpy::cpy::Py_DecRef(__m); return nullptr; }}\n")
        out.write("            ::tpy::cpy::Py_DecRef(__c);\n")
        out.write("        }\n")

    def generate_extension_glue(self, module: TpyModule, module_name: str) -> str:
        """Emit the CPython extension glue TU for an `# tpy: ext_module`:
        PyMethodDef/PyModuleDef/PyInit_ plus one wrapper per @export-ed
        function. Param/return types must be boundary-marshallable (sema
        already enforced this); the wrapper marshals via tpy::interop and the
        TU never includes Python.h
        (the facade keeps its macros out of TPy-generated code).
        """
        exposed = [f for f in module.functions if f.exposed_to_host]
        # The call target lives in the namespace the module body emitted into
        # (which is ctx.module_name, possibly "__main__"); the PyInit symbol
        # and m_name use the import name (the module_name param / file stem).
        call_ns = self.ctx.module_name
        if not module_name.isidentifier():
            raise CodeGenError(
                f"ext_module name '{module_name}' is not a valid CPython "
                f"module identifier (needed for PyInit_)")
        sym = escape_cpp_name(module_name)
        exposed_classes = self._exposed_classes(module, module_name, call_ns)
        exposed_enums = self._exposed_enums(module, module_name)
        exposed_constants = self._exposed_constants(module, call_ns)

        out = io.StringIO()
        out.write("// Generated by TurboPython Compiler -- CPython extension glue\n")
        out.write(f'#include "{module_to_include_path(call_ns)}"\n')
        out.write('#include "tpy/interop/marshal.hpp"\n')
        out.write('#include "tpy/interop/exc_bridge.hpp"\n')
        if exposed_classes:
            out.write('#include "tpy/interop/class_bridge.hpp"\n')
        if exposed_enums:
            out.write('#include "tpy/interop/enum_bridge.hpp"\n')
        out.write("\n")
        out.write("namespace {\n")
        out.write("using namespace ::tpy::cpy;\n\n")

        # User exception classes defined here each get their own Python type at
        # PyInit_; the registry lets set_py_err_from map a raised one to its
        # type instead of degrading to the built-in base. Omitted entirely when
        # the module defines none (keeps the common glue unchanged).
        user_excs = self._ordered_user_exc_classes(module, module_name, call_ns)
        registry = f"{sym}__exc_registry" if user_excs else None
        if registry:
            out.write(f"::tpy::interop::ExcRegistry {registry};\n\n")
        reg_arg = f", {registry}" if registry else ""

        # Module-static handle to each exposed class's / enum's CPython type,
        # assigned at PyInit_. Declared up front so a wrapper can reference a
        # class- or enum-typed param/return's type (instance_payload / enum value
        # marshalling) before the type-creation call appears below.
        for cls in exposed_classes:
            out.write(f"PyObject *{cls['var']} = nullptr;\n")
        for e in exposed_enums:
            out.write(f"PyObject *{e['var']} = nullptr;\n")
        if exposed_classes or exposed_enums:
            out.write("\n")

        def assert_marshal(t, what: str, fn) -> None:
            # Sema (_validate_ext_module_exports) already rejected unmarshallable
            # boundary types; this asserts the contract to catch sema/codegen
            # drift loudly rather than emit a TU that won't compile. Span[T]
            # mirrors sema's param-only admission (is_function_boundary_
            # marshallable stays Span-blind, direction-symmetric for every
            # other boundary type).
            allow_void = what == "return"
            if what != "return" and is_span_boundary_param(t):
                return
            assert is_function_boundary_marshallable(t, allow_void), \
                boundary_unmarshallable_msg(fn.name, what, boundary_type_name(t))

        wrappers: list[tuple[str, str, str, bool]] = []  # (pyname, wrap, flag, kw)
        for fn in exposed:
            assert_marshal(fn.return_type, "return", fn)
            for pname, ptype in fn.params:
                assert_marshal(ptype, f"parameter '{pname}'", fn)
            n = len(fn.params)
            wname = f"{sym}__{escape_cpp_name(fn.name)}_pywrap"
            call = qualified_cpp_name(call_ns, fn.name)
            if n == 0:
                meth, kw = "METH_NOARGS", False
                out.write(f"PyObject *{wname}(PyObject *self, PyObject *unused) {{\n")
            else:
                meth, kw = "METH_VARARGS | METH_KEYWORDS", True
                out.write(f"PyObject *{wname}(PyObject *self, PyObject *args, "
                          f"PyObject *kwargs) {{\n")
                self._emit_arg_unpack(out, [pn for pn, _t in fn.params],
                                      "nullptr", fn.name)
            wrappers.append((fn.name, wname, meth, kw))
            out.write("    try {\n")
            # Marshal each arg into a local before the call so conversion order
            # is left-to-right (C++ argument evaluation order is unspecified).
            argtoks = [self._emit_marshal_in(out, i, ptype, sym)
                       for i, (_pn, ptype) in enumerate(fn.params)]
            call_expr = f"{call}({', '.join(argtoks)})"
            self._emit_call_return(out, fn.return_type, call_expr, sym,
                                   self._param_alias_candidates(fn.params))
            self._emit_boundary_catch(out, reg_arg)
            out.write("}\n\n")

        self._emit_user_exc_setters(out, user_excs, sym)

        for cls in exposed_classes:
            self._emit_exposed_class(out, cls, sym, reg_arg)

        out.write(f"PyMethodDef {sym}__methods[] = {{\n")
        for pyname, wname, meth, kw in wrappers:
            slot = f"as_pycfunction({wname})" if kw else wname
            out.write(f'    {{"{pyname}", {slot}, {meth}, nullptr}},\n')
        out.write("    {nullptr, nullptr, 0, nullptr},\n")
        out.write("};\n\n")

        out.write(f"PyModuleDef {sym}__moduledef = {{\n")
        out.write("    MODULEDEF_HEAD_INIT,\n")
        out.write(f'    "{module_name}",\n')
        out.write("    nullptr,\n")
        out.write("    -1,\n")
        out.write(f"    {sym}__methods,\n")
        out.write("    nullptr, nullptr, nullptr, nullptr,\n")
        out.write("};\n")
        out.write("}  // namespace\n\n")

        # PyInit_ must be the literal import name; escape_cpp_name (used for
        # the internal symbols) would mangle a C++-keyword stem and break load.
        out.write(f'extern "C" PyObject *PyInit_{module_name}(void) {{\n')
        # A C++ exception escaping extern "C" is UB: convert init failures to
        # the NULL sentinel with a Python exception set.
        out.write("    try {\n")
        if user_excs or exposed_classes or exposed_enums or exposed_constants:
            # Module created before __tpy_init so the exception/class/enum types
            # exist (and are registered) even for a raise during module-init
            # top-level code. Each created type's ref is retained for the .so's
            # lifetime (the registry / the module dict keep it alive) -- like the
            # interpreter's own exception singletons. Constants are read AFTER
            # __tpy_init: the current Final kinds are static-init (constexpr /
            # static BigInt), so the order is not load-bearing today, but a
            # future runtime-initialized constant kind would need the init first.
            out.write(f"        PyObject *__m = ::tpy::cpy::PyModule_Create2("
                      f"&{sym}__moduledef, ::tpy::cpy::PYTHON_API_VERSION);\n")
            out.write("        if (!__m) return nullptr;\n")
            for e in user_excs:
                out.write(f'        PyObject *{e["var"]} = ::tpy::cpy::'
                          f'PyErr_NewException("{e["py_name"]}", {e["base_expr"]}, '
                          f"nullptr);\n")
                out.write(f'        if (!{e["var"]}) {{ ::tpy::cpy::Py_DecRef(__m); '
                          f"return nullptr; }}\n")
                # A failed AddObjectRef would leave the type unimportable by name
                # while still registered -- bail rather than ship that mismatch.
                out.write(f'        if (::tpy::cpy::PyModule_AddObjectRef(__m, '
                          f'"{e["py_name"].split(".")[-1]}", {e["var"]}) < 0) '
                          f"{{ ::tpy::cpy::Py_DecRef(__m); return nullptr; }}\n")
                out.write(f"        {registry}.push_back("
                          f'{{std::type_index(typeid({e["cpp_type"]})), '
                          f'{e["var"]}, {e["setter"]}}});\n')
            for c in exposed_classes:
                base = f"{sym}__{escape_cpp_name(c['simple'])}"
                if c["base_var"] is not None:
                    # Wire the exposed base as tp_base (created above -- the
                    # class list is ordered base-before-derived).
                    out.write(f'        {c["var"]} = ::tpy::cpy::'
                              f"PyType_FromSpecWithBases(&{base}__spec, "
                              f'{c["base_var"]});\n')
                else:
                    out.write(f'        {c["var"]} = ::tpy::cpy::PyType_FromSpec('
                              f"&{base}__spec);\n")
                out.write(f'        if (!{c["var"]}) {{ ::tpy::cpy::Py_DecRef(__m); '
                          f"return nullptr; }}\n")
                out.write(f'        if (::tpy::cpy::PyModule_AddObjectRef(__m, '
                          f'"{c["simple"]}", {c["var"]}) < 0) '
                          f"{{ ::tpy::cpy::Py_DecRef(__m); return nullptr; }}\n")
            for e in exposed_enums:
                self._emit_enum_create(out, e, module_name)
            out.write(f"        {qualified_cpp_name(call_ns, '__tpy_init')}();\n")
            for c in exposed_constants:
                self._emit_constant_add(out, c)
            out.write("        return __m;\n")
        else:
            out.write(f"        {qualified_cpp_name(call_ns, '__tpy_init')}();\n")
            out.write(f"        return ::tpy::cpy::PyModule_Create2(&{sym}__moduledef, "
                      f"::tpy::cpy::PYTHON_API_VERSION);\n")
        out.write("    } catch (const ::tpy::BaseException &__e) {\n")
        out.write(f"        ::tpy::interop::set_py_err_from(__e{reg_arg});\n")
        out.write("        return nullptr;\n")
        out.write("    } catch (...) {\n")
        out.write("        if (!::tpy::cpy::PyErr_Occurred())\n")
        out.write("            ::tpy::cpy::PyErr_SetString("
                  "::tpy::cpy::PyExc_RuntimeError,\n")
        out.write('                "tpy extension: module initialization failed");\n')
        out.write("        return nullptr;\n")
        out.write("    }\n")
        out.write("}\n")
        return out.getvalue()
