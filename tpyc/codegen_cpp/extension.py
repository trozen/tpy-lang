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

from ..parse import TpyModule
from ..typesys import TpyType, is_void_like_type
from ..type_def_registry import (
    is_boundary_marshallable, is_exposed_class, _boundary_inner,
    boundary_cpp_type, boundary_unmarshallable_msg,
)
from .context import (
    qualified_cpp_name, escape_cpp_name, module_to_include_path, CodeGenError)

if TYPE_CHECKING:
    from .context import CodeGenContext
    from .records import RecordGenerator


class ExtensionGenerator:
    """Emits the CPython extension glue TU for an `# tpy: ext_module`."""

    def __init__(self, ctx: CodeGenContext, records: RecordGenerator):
        self.ctx = ctx
        self.records = records  # shares the inheritance topo sort for exc ordering

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
            result.append({
                "var": var_for(record.name),
                "cpp_type": qualified_cpp_name(call_ns, record.name),
                "py_name": f"{module_name}.{record.name}",
                "base_expr": base_expr,
            })
        return result

    def _exposed_classes(self, module: TpyModule, module_name: str,
                         call_ns: str) -> list[dict]:
        """User classes marked `@export` in this ext_module. Each drives one
        PyType_FromSpec type created + registered at PyInit_ and one set of
        method/getset wrappers. (Disjoint from _ordered_user_exc_classes: the
        sema validator rejects @export on a throwable.)"""
        reg = self.ctx.analyzer.registry
        sym = escape_cpp_name(module_name)
        result = []
        for r in module.records:
            if not r.exposed_to_host:
                continue
            result.append({
                "record": r,
                "info": reg.get_record(r.name),
                "var": f"{sym}__type_{escape_cpp_name(r.name)}",
                "cpp_type": qualified_cpp_name(call_ns, r.name),
                "py_name": f"{module_name}.{r.name}",
                "simple": r.name,
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

    def _emit_marshal_in(self, out: TextIO, idx: int, typ: TpyType,
                         sym: str) -> str:
        """Marshal arg a{idx} into a local; return the token to pass at the
        call. A class param binds a reference to the live embedded payload --
        the borrow that makes mutation through it write through to the same
        PyObject; a scalar/str/bytes param copies in via from_py."""
        if is_exposed_class(typ):
            cpp, tv = self._class_cpp_var(typ, sym)
            out.write(f"        {cpp} &__p{idx} = "
                      f"*::tpy::interop::instance_payload<{cpp}>("
                      f"a{idx}, (::tpy::cpy::PyTypeObject *){tv});\n")
        else:
            cpp = boundary_cpp_type(_boundary_inner(typ))
            out.write(f"        {cpp} __p{idx} = "
                      f"::tpy::interop::from_py<{cpp}>(a{idx});\n")
        return f"__p{idx}"

    def _emit_call_return(self, out: TextIO, ret_typ: TpyType | None,
                          call_expr: str, sym: str) -> None:
        """Emit the return of a boundary call: void -> None; an exposed class ->
        a fresh wrapping instance (instance_to_py -- identity NOT preserved);
        else to_py."""
        if ret_typ is None or is_void_like_type(ret_typ):
            out.write(f"        {call_expr};\n")
            out.write("        return ::tpy::interop::none_to_py();\n")
        elif is_exposed_class(ret_typ):
            _cpp, tv = self._class_cpp_var(ret_typ, sym)
            out.write(f"        return ::tpy::interop::instance_to_py("
                      f"(::tpy::cpy::PyTypeObject *){tv}, {call_expr});\n")
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
        init_params = [(p.name, p.type) for p in info.methods["__init__"][0].params
                       if p.name != "self"] if "__init__" in info.methods else []
        n_init = len(init_params)
        init_fn = f"{sym}__{escape_cpp_name(cls['simple'])}_init"
        kw_param = "kwargs" if n_init else ""
        out.write(f"int {init_fn}(PyObject *self, PyObject *args, "
                  f"PyObject *{kw_param}) {{\n")
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
            self._emit_call_return(out, m.return_type, call, sym)
            self._emit_boundary_catch(out, reg_arg)
            out.write("}\n\n")

        # getset: every annotated field as a read/write descriptor. Field types
        # are scalar/str/bytes (the sema validator deferred class-typed fields),
        # so the get copies a fresh PyObject and the set marshals back in.
        getset_entries: list[tuple[str, str, str]] = []
        for fld in info.fields:
            fcpp = escape_cpp_name(fld.name)
            getn = f"{sym}__{escape_cpp_name(cls['simple'])}__{fcpp}_get"
            setn = f"{sym}__{escape_cpp_name(cls['simple'])}__{fcpp}_set"
            getset_entries.append((fld.name, getn, setn))
            out.write(f"PyObject *{getn}(PyObject *self, void *) {{\n")
            out.write("    try {\n")
            out.write(f"        return ::tpy::interop::to_py("
                      f"{cppvar}->payload.{fcpp});\n")
            out.write("    } catch (...) {\n")
            out.write("        if (!PyErr_Occurred())\n")
            out.write('            PyErr_SetString(PyExc_RuntimeError, '
                      '"tpy extension: attribute read failed");\n')
            out.write("        return nullptr;\n")
            out.write("    }\n}\n")
            fcpp_type = boundary_cpp_type(_boundary_inner(fld.type))
            out.write(f"int {setn}(PyObject *self, PyObject *value, void *) {{\n")
            out.write("    try {\n")
            out.write(f"        {cppvar}->payload.{fcpp} = "
                      f"::tpy::interop::from_py<{fcpp_type}>(value);\n")
            out.write("        return 0;\n")
            out.write("    } catch (...) {\n")
            out.write("        if (!PyErr_Occurred())\n")
            out.write('            PyErr_SetString(PyExc_RuntimeError, '
                      '"tpy extension: attribute write failed");\n')
            out.write("        return -1;\n")
            out.write("    }\n}\n\n")

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
        out.write("    {Py_tp_new, (void *)::tpy::cpy::PyType_GenericNew},\n")
        out.write("    {0, nullptr},\n};\n")
        out.write(f"PyType_Spec {base}__spec = {{\n")
        out.write(f'    "{cls["py_name"]}", '
                  f"(int)sizeof(::tpy::interop::Instance<{cpp}>), 0,\n")
        out.write(f"    Py_TPFLAGS_DEFAULT, {base}__slots,\n}};\n\n")

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

        out = io.StringIO()
        out.write("// Generated by TurboPython Compiler -- CPython extension glue\n")
        out.write(f'#include "{module_to_include_path(call_ns)}"\n')
        out.write('#include "tpy/interop/marshal.hpp"\n')
        out.write('#include "tpy/interop/exc_bridge.hpp"\n')
        if exposed_classes:
            out.write('#include "tpy/interop/class_bridge.hpp"\n')
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

        # Module-static handle to each exposed class's CPython type, assigned at
        # PyInit_ (PyType_FromSpec). Declared up front so the function/method
        # wrappers can reference a class-typed param/return's type before its
        # PyType_FromSpec call appears.
        for cls in exposed_classes:
            out.write(f"PyObject *{cls['var']} = nullptr;\n")
        if exposed_classes:
            out.write("\n")

        def assert_marshal(t, what: str, fn) -> None:
            # Sema (_validate_ext_module_exports) already rejected unmarshallable
            # boundary types; this asserts the contract to catch sema/codegen
            # drift loudly rather than emit a TU that won't compile.
            allow_void = what == "return"
            assert is_boundary_marshallable(t, allow_void), \
                boundary_unmarshallable_msg(fn.name, what, boundary_cpp_type(t))

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
            self._emit_call_return(out, fn.return_type, call_expr, sym)
            self._emit_boundary_catch(out, reg_arg)
            out.write("}\n\n")

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
        if user_excs or exposed_classes:
            # Module created before __tpy_init so the exception/class types exist
            # (and are registered) even for a raise during module-init top-level
            # code. Each created type's ref is retained for the .so's lifetime
            # (the registry / the module dict keep it alive) -- like the
            # interpreter's own exception singletons.
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
                          f'{{std::type_index(typeid({e["cpp_type"]})), {e["var"]}}});\n')
            for c in exposed_classes:
                base = f"{sym}__{escape_cpp_name(c['simple'])}"
                out.write(f'        {c["var"]} = ::tpy::cpy::PyType_FromSpec('
                          f"&{base}__spec);\n")
                out.write(f'        if (!{c["var"]}) {{ ::tpy::cpy::Py_DecRef(__m); '
                          f"return nullptr; }}\n")
                out.write(f'        if (::tpy::cpy::PyModule_AddObjectRef(__m, '
                          f'"{c["simple"]}", {c["var"]}) < 0) '
                          f"{{ ::tpy::cpy::Py_DecRef(__m); return nullptr; }}\n")
            out.write(f"        {qualified_cpp_name(call_ns, '__tpy_init')}();\n")
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
