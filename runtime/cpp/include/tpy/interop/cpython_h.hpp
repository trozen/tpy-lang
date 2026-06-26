#pragma once
// CPython limited-API (abi3) facade for TPy extension glue: the struct
// layouts (cpython_abi.hpp) plus the constants and extern "C" function
// declarations the generated glue calls.
//
// We deliberately do NOT `#include <Python.h>`. Python.h's macro surface
// would collide with TPy module-level constants in any generated TU that also
// includes tpy.hpp (the extension glue TU includes both the module header and
// this facade, so the worlds necessarily meet here). Mirroring only the
// limited-API symbols we use keeps Python.h out of every TPy-generated TU.
//
// The function declarations have C language linkage, so the loader resolves
// them against the host interpreter at import time (the .so links with
// undefined Python symbols, no libpython link on Linux/macOS).
//
// abi3 floor: Python 3.12 (Py_LIMITED_API=0x030c0000). Layout correctness is
// guarded by the cpython_abi.hpp self-check; the constant values below are
// asserted against Python.h's macros there too. The self-check does NOT yet
// validate these function signatures (TODO) -- a wrong parameter or return
// type here is silent until link/ABI corruption, so mirror each decl against
// the real limited-API prototype by hand.

#include "tpy/interop/cpython_abi.hpp"

namespace tpy::cpy {

extern "C" {

PyObject *PyModule_Create2(PyModuleDef *def, int module_api_version);

// Refcount + error state.
void Py_DecRef(PyObject *o);
void Py_IncRef(PyObject *o);
PyObject *PyErr_Occurred(void);
void PyErr_SetString(PyObject *type, const char *message);

// Argument unpacking: glue uses only the "O" code (raw borrowed PyObject*);
// from_py<T> owns every conversion, so format codes never appear.
int PyArg_ParseTuple(PyObject *args, const char *format, ...);

// int marshalling (the fixed-width int types + BigInt). The signed path and
// the unsigned widths <= 32 bits go through the long long accessors (range
// checked against the narrower target); UInt64 needs the unsigned long long
// accessors because its top half does not fit a signed long long.
PyObject *PyLong_FromLongLong(long long v);
long long PyLong_AsLongLongAndOverflow(PyObject *o, int *overflow);
PyObject *PyLong_FromUnsignedLongLong(unsigned long long v);
unsigned long long PyLong_AsUnsignedLongLong(PyObject *o);
PyObject *PyNumber_Index(PyObject *o);          // __index__ coercion
PyObject *PyNumber_ToBase(PyObject *o, int base);  // BigInt slow path (hex)
PyObject *PyLong_FromString(const char *str, char **pend, int base);
const char *PyUnicode_AsUTF8AndSize(PyObject *unicode, Py_ssize_t *size);

// float marshalling (double). PyFloat_AsDouble coerces via __float__, so int /
// bool arguments cross like CPython; it returns -1.0 + sets an exception on
// failure.
PyObject *PyFloat_FromDouble(double v);
double PyFloat_AsDouble(PyObject *o);

// bool marshalling. PyObject_IsTrue coerces any object via truthiness (so a
// bool param accepts arbitrary args like CPython does), returning 1 / 0 / -1
// (-1 sets an exception). PyBool_FromLong returns a new ref to Py_True/Py_False.
int PyObject_IsTrue(PyObject *o);
PyObject *PyBool_FromLong(long v);

// Stable-ABI exception singletons (provided by the host interpreter).
extern PyObject *PyExc_RuntimeError;
extern PyObject *PyExc_TypeError;
extern PyObject *PyExc_OverflowError;

// The None singleton. Py_None is the macro `&_Py_NoneStruct`; we mirror the
// underlying data symbol so void-return wrappers can hand back a fresh ref.
extern PyObject _Py_NoneStruct;

}  // extern "C"

}  // namespace tpy::cpy
