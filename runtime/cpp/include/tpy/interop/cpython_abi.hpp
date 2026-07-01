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
// METH_KEYWORDS wrapper shape (self, args, kwargs). Stored in PyMethodDef.ml_meth
// after a cast through `as_pycfunction`; the runtime dispatches on the flag.
using PyCFunctionWithKeywords = PyObject *(*)(PyObject *, PyObject *, PyObject *);

// Cast a keyword wrapper to the PyCFunction slot type. Routed through
// `void(*)(void)` because converting between incompatible function-pointer
// types directly trips -Wcast-function-type (on under -Wextra -Werror), exactly
// as CPython's own _PyCFunction_CAST avoids it.
inline PyCFunction as_pycfunction(PyCFunctionWithKeywords f) {
    return reinterpret_cast<PyCFunction>(reinterpret_cast<void (*)()>(f));
}

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
inline constexpr int METH_KEYWORDS = 0x0002;  // ml_meth is PyCFunctionWithKeywords
inline constexpr int METH_NOARGS = 0x0004;

// PyModule_Create is a macro for PyModule_Create2(def, PYTHON_API_VERSION).
inline constexpr int PYTHON_API_VERSION = 1013;

// --- Heap-type creation (PyType_FromSpec), for exposed classes. ---

// Slot function-pointer types (limited-API, abi3-stable signatures).
using getter = PyObject *(*)(PyObject *, void *);
using setter = int (*)(PyObject *, PyObject *, void *);

// One (slot-id, function-pointer) pair; a PyType_Spec.slots array is terminated
// by a {0, nullptr} entry.
struct PyType_Slot {
    int slot;
    void *pfunc;
};

struct PyType_Spec {
    const char *name;
    int basicsize;
    int itemsize;
    unsigned int flags;
    PyType_Slot *slots;
};

// A read/write descriptor (exposed field). get/set are typed function pointers;
// closure is unused (nullptr).
struct PyGetSetDef {
    const char *name;
    getter get;
    setter set;
    const char *doc;
    void *closure;
};

// Type-slot ids the class glue populates (values from CPython's typeslots.h,
// stable across versions). Checked against Python.h in the facade self-check.
inline constexpr int Py_tp_dealloc = 52;
inline constexpr int Py_tp_hash = 59;
inline constexpr int Py_tp_init = 60;
inline constexpr int Py_tp_methods = 64;
inline constexpr int Py_tp_new = 65;
inline constexpr int Py_tp_repr = 66;
inline constexpr int Py_tp_richcompare = 67;
inline constexpr int Py_tp_str = 70;
inline constexpr int Py_tp_getset = 73;
inline constexpr int Py_tp_free = 74;

// Dunder-slot function-pointer types (limited-API, abi3-stable signatures).
using reprfunc = PyObject *(*)(PyObject *);
using hashfunc = Py_ssize_t (*)(PyObject *);
using richcmpfunc = PyObject *(*)(PyObject *, PyObject *, int);
using unaryfunc = PyObject *(*)(PyObject *);
using binaryfunc = PyObject *(*)(PyObject *, PyObject *);
using ternaryfunc = PyObject *(*)(PyObject *, PyObject *, PyObject *);  // nb_power(base, exp, mod)

// PyObject_RichCompare op codes (Python.h's Py_LT..Py_GE), passed as
// richcmpfunc's third argument. Checked against Python.h in the self-check.
inline constexpr int Py_LT = 0;
inline constexpr int Py_LE = 1;
inline constexpr int Py_EQ = 2;
inline constexpr int Py_NE = 3;
inline constexpr int Py_GT = 4;
inline constexpr int Py_GE = 5;

// Arithmetic/ordering operator slot ids. Values from
// CPython's typeslots.h, stable across versions; checked against Python.h in
// the facade self-check.
inline constexpr int Py_nb_add = 7;
inline constexpr int Py_nb_and = 8;
inline constexpr int Py_nb_floor_divide = 12;
inline constexpr int Py_nb_inplace_add = 14;
inline constexpr int Py_nb_inplace_and = 15;
inline constexpr int Py_nb_inplace_floor_divide = 16;
inline constexpr int Py_nb_inplace_lshift = 17;
inline constexpr int Py_nb_inplace_multiply = 18;
inline constexpr int Py_nb_inplace_or = 19;
inline constexpr int Py_nb_inplace_remainder = 21;
inline constexpr int Py_nb_inplace_rshift = 22;
inline constexpr int Py_nb_inplace_subtract = 23;
inline constexpr int Py_nb_inplace_true_divide = 24;
inline constexpr int Py_nb_inplace_xor = 25;
inline constexpr int Py_nb_invert = 27;
inline constexpr int Py_nb_lshift = 28;
inline constexpr int Py_nb_multiply = 29;
inline constexpr int Py_nb_negative = 30;
inline constexpr int Py_nb_or = 31;
inline constexpr int Py_nb_positive = 32;
inline constexpr int Py_nb_power = 33;
inline constexpr int Py_nb_remainder = 34;
inline constexpr int Py_nb_rshift = 35;
inline constexpr int Py_nb_subtract = 36;
inline constexpr int Py_nb_true_divide = 37;
inline constexpr int Py_nb_xor = 38;

// Container-protocol slot ids + their function-pointer
// types. binaryfunc (mp_subscript) and unaryfunc (tp_iter/tp_iternext) are
// already declared above; lenfunc/objobjargproc/objobjproc are new here.
using lenfunc = Py_ssize_t (*)(PyObject *);
using objobjargproc = int (*)(PyObject *, PyObject *, PyObject *);  // mp_ass_subscript(o, key, value); value==nullptr deletes
using objobjproc = int (*)(PyObject *, PyObject *);  // sq_contains(o, value) -> -1/0/1
inline constexpr int Py_mp_ass_subscript = 3;
inline constexpr int Py_mp_length = 4;
inline constexpr int Py_mp_subscript = 5;
inline constexpr int Py_sq_contains = 41;
inline constexpr int Py_sq_length = 45;
inline constexpr int Py_tp_iter = 62;
inline constexpr int Py_tp_iternext = 63;

// Exposed classes are final (not subclassable from Python): partial
// subclassability would silently diverge in method dispatch (the C++ payload
// method is called directly, bypassing a Python override). Under the limited
// API Py_TPFLAGS_DEFAULT is 0; spelling it documents the choice and matches
// CPython's macro (asserted in the self-check).
inline constexpr unsigned int Py_TPFLAGS_DEFAULT = 0;

// Py_TYPE: the limited API hides the macro, but ob_type is a stable PyObject
// field, so read it directly.
inline PyTypeObject *Py_TYPE(PyObject *o) { return o->ob_type; }

// --- Buffer protocol (Span[T] numeric marshalling). ---
// Py_buffer layout has been part of the stable abi3 since Python 3.11 (the
// struct is fully public by design -- the whole point of the buffer protocol
// is direct field access by the consumer).
struct Py_buffer {
    void *buf;
    PyObject *obj;        // owned reference
    Py_ssize_t len;
    Py_ssize_t itemsize;
    int readonly;
    int ndim;
    char *format;
    Py_ssize_t *shape;
    Py_ssize_t *strides;
    Py_ssize_t *suboffsets;
    void *internal;
};

// Flags for PyObject_GetBuffer. span_from_py requests ND (shape, and implies
// the exporter must be C-contiguous since no strides are requested) | FORMAT
// (populate view.format for the element-type check).
inline constexpr int PyBUF_ND = 0x0008;
inline constexpr int PyBUF_FORMAT = 0x0004;

}  // namespace tpy::cpy
