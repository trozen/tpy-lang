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
 */

#pragma once

#include "tpy/core.hpp"
#include "tpy/interop/cpython_h.hpp"

namespace tpy::interop {

// Most-derived first: the first matching dynamic_cast wins, so leaves must
// precede their bases.
inline cpy::PyObject *py_exc_for(const tpy::BaseException &e) noexcept {
#define TPY_EXC_MAP(Name) \
    if (dynamic_cast<const ::tpy::Name *>(&e)) return ::tpy::cpy::PyExc_##Name;
    TPY_EXC_MAP(BrokenPipeError)
    TPY_EXC_MAP(ConnectionResetError)
    TPY_EXC_MAP(ConnectionRefusedError)
    TPY_EXC_MAP(ConnectionAbortedError)
    TPY_EXC_MAP(ConnectionError)
    TPY_EXC_MAP(FileNotFoundError)
    TPY_EXC_MAP(PermissionError)
    TPY_EXC_MAP(BlockingIOError)
    TPY_EXC_MAP(FileExistsError)
    TPY_EXC_MAP(NotADirectoryError)
    TPY_EXC_MAP(IsADirectoryError)
    TPY_EXC_MAP(TimeoutError)
    TPY_EXC_MAP(OSError)
    TPY_EXC_MAP(IndexError)
    TPY_EXC_MAP(KeyError)
    TPY_EXC_MAP(LookupError)
    TPY_EXC_MAP(ZeroDivisionError)
    TPY_EXC_MAP(OverflowError)
    TPY_EXC_MAP(FloatingPointError)
    TPY_EXC_MAP(ArithmeticError)
    TPY_EXC_MAP(RecursionError)
    TPY_EXC_MAP(RuntimeError)
    TPY_EXC_MAP(ValueError)
    TPY_EXC_MAP(AttributeError)
    TPY_EXC_MAP(AssertionError)
    TPY_EXC_MAP(TypeError)
    TPY_EXC_MAP(NotImplementedError)
    TPY_EXC_MAP(EOFError)
    TPY_EXC_MAP(MemoryError)
    TPY_EXC_MAP(StopIteration)
    TPY_EXC_MAP(StopAsyncIteration)
    TPY_EXC_MAP(GeneratorExit)
    TPY_EXC_MAP(KeyboardInterrupt)
    TPY_EXC_MAP(Exception)
#undef TPY_EXC_MAP
    return ::tpy::cpy::PyExc_BaseException;
}

// No PyErr_Occurred guard: the glue only reaches this clause for body-raised
// exceptions (the marshaller throws a non-BaseException marker), so no error is
// set yet and the mapped type+message must win.
inline void set_py_err_from(const tpy::BaseException &e) noexcept {
    cpy::PyErr_SetString(py_exc_for(e), e.what());
}

}  // namespace tpy::interop
