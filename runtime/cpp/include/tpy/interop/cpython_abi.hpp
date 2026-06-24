#pragma once
// Hand-mirrored CPython limited-API (abi3) struct layouts -- the ABI subset
// of the facade. Split out from cpython_h.hpp (which adds the constants and
// extern "C" function declarations) for one reason: the layout self-check TU
// (runtime/cpp/tests/interop/cpython_facade_selfcheck.cpp) must include BOTH
// real <Python.h> and our mirror to assert they match, and Python.h's
// #define'd constants (METH_*, PYTHON_API_VERSION) plus its extern "C"
// function prototypes would collide with ours -- so the self-check includes
// only THIS header (pure struct layouts, no macro-named identifiers, no
// function decls) and checks constant values against Python.h's macros
// directly.
//
// Everything is under `namespace tpy::cpy`, so our struct names never clash
// with Python.h's global ones in that shared TU.
//
// abi3 floor: Python 3.12. The limited API guarantees these layouts are
// stable; the self-check is the guard against a hand-mirroring slip.

#include <cstdint>

namespace tpy::cpy {

using Py_ssize_t = std::intptr_t;

// Opaque -- only ever held by pointer.
struct PyTypeObject;
struct PyModuleDef_Slot;

// Stable-ABI PyObject head. (ob_refcnt, ob_type) layout is part of the
// limited API; the 3.12+ immortality refcount union is non-limited and does
// not change this field's size or offset.
struct PyObject {
    Py_ssize_t ob_refcnt;
    PyTypeObject *ob_type;
};

using PyCFunction = PyObject *(*)(PyObject *, PyObject *);

struct PyMethodDef {
    const char *ml_name;
    PyCFunction ml_meth;
    int ml_flags;
    const char *ml_doc;
};

// Trailing module-def members are generic function pointers -- all function
// pointers share one size on every supported target, so the layout matches;
// we only ever zero-initialize them.
using _py_init_func = PyObject *(*)();
using _py_visitproc = int (*)(PyObject *, void *);
using _py_traverseproc = int (*)(PyObject *, _py_visitproc, void *);
using _py_inquiry = int (*)(PyObject *);
using _py_freefunc = void (*)(void *);

struct PyModuleDef_Base {
    PyObject ob_base;
    _py_init_func m_init;
    Py_ssize_t m_index;
    PyObject *m_copy;
};

struct PyModuleDef {
    PyModuleDef_Base m_base;
    const char *m_name;
    const char *m_doc;
    Py_ssize_t m_size;
    PyMethodDef *m_methods;
    PyModuleDef_Slot *m_slots;
    _py_traverseproc m_traverse;
    _py_inquiry m_clear;
    _py_freefunc m_free;
};

// PyModuleDef_HEAD_INIT: a zeroed base with ob_refcnt == 1.
inline constexpr PyModuleDef_Base MODULEDEF_HEAD_INIT = {
    /*ob_base*/ {/*ob_refcnt*/ 1, /*ob_type*/ nullptr},
    /*m_init*/ nullptr, /*m_index*/ 0, /*m_copy*/ nullptr};

// PyMethodDef.ml_flags values. (Kept here, with the layouts, so the facade
// self-check can verify them against Python.h without pulling cpython_h.hpp's
// extern "C" decls.)
inline constexpr int METH_VARARGS = 0x0001;
inline constexpr int METH_NOARGS = 0x0004;

// PyModule_Create is a macro for PyModule_Create2(def, PYTHON_API_VERSION).
inline constexpr int PYTHON_API_VERSION = 1013;

}  // namespace tpy::cpy
