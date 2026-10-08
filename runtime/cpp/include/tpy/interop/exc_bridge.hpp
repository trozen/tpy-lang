/**
 * TurboPython Runtime -- CPython exception bridge
 *
 * Translates a tpy::BaseException escaping an @export body (or module init)
 * into the matching CPython exception, preserving both the concrete type and
 * the message. The generated glue catches `const tpy::BaseException&` and
 * calls set_py_err_from(); a non-BaseException C++ exception (incl. the
 * marshaller's MarshalError, which pre-sets PyErr) still flows through the
 * glue's `catch(...)` path.
 *
 * The cascade lives here -- not in core.hpp -- so the Python C-API stays out
 * of the core exception taxonomy. Every tpy exception name maps 1:1 onto the
 * PyExc_<sameName> singleton, so a new core.hpp subclass that is not listed
 * here degrades gracefully to its nearest listed base (e.g. an unlisted
 * OSError subclass surfaces as PyExc_OSError).
 *
 * Limitation: BaseException subclasses with no PyExc_* counterpart degrade to
 * PyExc_BaseException -- notably async.hpp's CancelledError, since CPython's
 * asyncio.CancelledError is a Python-level class with no stable-ABI singleton.
 *
 * User-defined exception classes get their own Python type at PyInit_ (the glue
 * populates an ExcRegistry mapping each C++ exception type to its PyObject*);
 * set_py_err_from consults the registry by exact dynamic type before falling to
 * the built-in cascade, so a user `class NotFound(KeyError)` surfaces as its own
 * type rather than degrading to KeyError.
 */

#pragma once

#include <cstdint>
#include <string>
#include <string_view>
#include <tuple>
#include <typeindex>
#include <typeinfo>
#include <variant>
#include <vector>

#include "tpy/core.hpp"
#include "tpy/interop/cpython_h.hpp"
#include "tpy/system.hpp"

namespace tpy::interop {

// The built-in exception taxonomy, most-derived first (leaves before bases, so
// the first dynamic_cast match wins). One list, two consumers below: the
// dynamic-type cascade and the by-name lookup the glue uses to resolve a user
// exception's built-in base.
#define TPY_EXC_LIST(X) \
    X(BrokenPipeError) X(ConnectionResetError) X(ConnectionRefusedError) \
    X(ConnectionAbortedError) X(ConnectionError) X(FileNotFoundError) \
    X(PermissionError) X(BlockingIOError) X(FileExistsError) \
    X(NotADirectoryError) X(IsADirectoryError) X(ChildProcessError) \
    X(InterruptedError) X(ProcessLookupError) X(TimeoutError) X(OSError) \
    X(IndexError) X(KeyError) X(LookupError) X(ZeroDivisionError) \
    X(OverflowError) X(FloatingPointError) X(ArithmeticError) X(RecursionError) \
    X(RuntimeError) X(ValueError) X(AttributeError) X(AssertionError) \
    X(TypeError) X(NotImplementedError) X(EOFError) X(MemoryError) \
    X(StopAsyncIteration) X(GeneratorExit) \
    X(KeyboardInterrupt) X(SystemExit) X(Exception)

// The per-type "raise this across the boundary" action: given the caught
// exception and its Python type, set the Python error. A message-only exception
// uses the default below (PyErr_SetString); a data-carrying one uses a glue-
// generated setter that constructs an instance, marshals each data field to an
// instance attribute, and PyErr_SetObject's it.
using ExcSetErr = void (*)(const tpy::BaseException &, cpy::PyObject *) noexcept;

// Maps a user exception's C++ type to its Python type + setter, populated once
// at PyInit_. A vector (not a map) -- a module has a handful of exception
// classes, and exact type_index match is the only query. Each glue TU owns one
// static registry holding strong PyObject* refs for the .so's lifetime (the
// type objects live forever, like the interpreter's own exception singletons);
// type_index keys are globally unique, so multiple ext modules coexisting in
// one interpreter never collide.
using ExcRegistry =
    std::vector<std::tuple<std::type_index, cpy::PyObject *, ExcSetErr>>;

// The constructor arguments the host-side instance is built from: `(message,)`,
// except that a SystemExit (or subclass) crosses as its `code`, so the host's
// `e.code` is the int / str / None the TPy side raised -- `(code,)`, or `()`
// for a no-argument SystemExit, which the empty message tells apart from
// SystemExit(None) (whose str is 'None'). A new reference, or nullptr with the
// Python error set.
inline cpy::PyObject *exc_ctor_args(const tpy::BaseException &e) noexcept {
    const auto *se = dynamic_cast<const tpy::SystemExit *>(&e);
    if (!se) return cpy::Py_BuildValue("(s)", e.what());
    if (const auto *i = std::get_if<int32_t>(&se->code)) {
        return cpy::Py_BuildValue("(N)", cpy::PyLong_FromLongLong(*i));
    }
    if (const auto *s = std::get_if<std::string>(&se->code)) {
        return cpy::Py_BuildValue(
            "(N)", cpy::PyUnicode_FromStringAndSize(
                       s->data(), static_cast<cpy::Py_ssize_t>(s->size())));
    }
    if (se->message.empty()) return cpy::Py_BuildValue("()");
    return cpy::Py_BuildValue("(O)", &cpy::_Py_NoneStruct);
}

// The default setter: no data attributes. Used for message-only user exceptions
// and for built-in exception types. A tuple value is unpacked into the
// constructor call when the error is normalized.
inline void exc_set_err_message_only(const tpy::BaseException &e,
                                     cpy::PyObject *pytype) noexcept {
    cpy::PyObject *args = exc_ctor_args(e);
    if (!args) return;
    cpy::PyErr_SetObject(pytype, args);
    cpy::Py_DecRef(args);
}

// Maps a body-raised built-in exception to its PyExc_* by exact-then-base
// dynamic type. An unlisted subclass degrades to its nearest listed base.
inline cpy::PyObject *py_exc_for(const tpy::BaseException &e) noexcept {
#define TPY_EXC_ENTRY(Name) \
    if (dynamic_cast<const ::tpy::Name *>(&e)) return ::tpy::cpy::PyExc_##Name;
    TPY_EXC_LIST(TPY_EXC_ENTRY)
#undef TPY_EXC_ENTRY
    return ::tpy::cpy::PyExc_BaseException;
}

// Resolves a built-in exception name (a user exception's base) to its PyExc_*.
// BaseException is the cascade tail, not a TPY_EXC_LIST entry, so name it here;
// any other unknown name (an exotic non-core base, e.g. CancelledError) degrades
// to PyExc_Exception -- the user type still gets a distinct, catchable identity.
inline cpy::PyObject *py_exc_by_name(std::string_view name) noexcept {
    if (name == "BaseException") return ::tpy::cpy::PyExc_BaseException;
#define TPY_EXC_ENTRY(Name) if (name == #Name) return ::tpy::cpy::PyExc_##Name;
    TPY_EXC_LIST(TPY_EXC_ENTRY)
#undef TPY_EXC_ENTRY
    return ::tpy::cpy::PyExc_Exception;
}
#undef TPY_EXC_LIST

// No PyErr_Occurred guard: the glue only reaches this clause for body-raised
// exceptions (the marshaller throws a non-BaseException marker), so no error is
// set yet and the mapped type+message must win.
inline void set_py_err_from(const tpy::BaseException &e) noexcept {
    exc_set_err_message_only(e, py_exc_for(e));
}

// Iterator exhaustion arrives as the error value of `__next__`'s std::expected,
// never as a thrown exception; the registry is accepted and ignored so the
// glue's call site is the same with or without user exception types.
inline void set_py_err_from(const tpy::StopIteration &) noexcept {
    cpy::PyErr_SetNone(cpy::PyExc_StopIteration);
}
inline void set_py_err_from(const tpy::StopIteration &e,
                            const ExcRegistry &) noexcept {
    set_py_err_from(e);
}

// A user exception matches by EXACT dynamic type (typeid on the polymorphic
// base), so its own Python type + setter win; an unregistered type (built-in, or
// a user exc not defined in this module) falls to the built-in cascade.
inline void set_py_err_from(const tpy::BaseException &e,
                            const ExcRegistry &reg) noexcept {
    const std::type_index key(typeid(e));
    for (const auto &[ti, pytype, set_err] : reg) {
        if (ti == key) {
            set_err(e, pytype);
            return;
        }
    }
    set_py_err_from(e);
}

}  // namespace tpy::interop
