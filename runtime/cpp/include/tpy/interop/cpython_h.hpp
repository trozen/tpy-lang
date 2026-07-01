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

// Module-init: create a Python exception type (`base` may be NULL -> Exception)
// and add an object to the module (AddObjectRef does not steal `value`, unlike
// the legacy AddObject; returns 0 on success).
PyObject *PyErr_NewException(const char *name, PyObject *base, PyObject *dict);
int PyModule_AddObjectRef(PyObject *module, const char *name, PyObject *value);

// Enum construction (exposed @export enums). The enum_bridge recreates a TPy
// enum as a CPython IntEnum/Enum via the stdlib `enum` module's functional API:
// import `enum`, fetch the base, then call it with a {name: value} dict and a
// `module=` kwarg. PyDict_SetItemString does not steal `item`; Py_BuildValue's
// "O" code adds its own reference; PyObject_Call invokes with an args tuple and
// a kwargs dict.
PyObject *PyImport_ImportModule(const char *name);
PyObject *PyObject_GetAttrString(PyObject *o, const char *attr);
PyObject *PyObject_Call(PyObject *callable, PyObject *args, PyObject *kwargs);
PyObject *Py_BuildValue(const char *format, ...);
PyObject *PyDict_New(void);
int PyDict_SetItemString(PyObject *dp, const char *key, PyObject *item);
// Enum value marshalling: an @export enum param checks the arg is an instance
// of the module's enum type (1 / 0 / -1-on-error) before reading its `.value`.
int PyObject_IsInstance(PyObject *inst, PyObject *cls);

// Heap-type creation for exposed classes. PyType_FromSpec builds the type from
// the slot table; the resulting type's tp_alloc is PyType_GenericAlloc (used by
// instance_to_py to mint a fresh instance). PyType_GetSlot fetches tp_free in
// the generic deallocator; PyType_IsSubtype backs the instance type-check.
PyObject *PyType_FromSpec(PyType_Spec *spec);
PyObject *PyType_GenericNew(PyTypeObject *type, PyObject *args, PyObject *kwds);
PyObject *PyType_GenericAlloc(PyTypeObject *type, Py_ssize_t nitems);
void *PyType_GetSlot(PyTypeObject *type, int slot);
int PyType_IsSubtype(PyTypeObject *a, PyTypeObject *b);

// Refcount + error state.
void Py_DecRef(PyObject *o);
void Py_IncRef(PyObject *o);
PyObject *PyErr_Occurred(void);
void PyErr_SetString(PyObject *type, const char *message);

// Argument unpacking: glue uses only the "O" code (raw borrowed PyObject*);
// from_py<T> owns every conversion, so format codes never appear. The keyword
// form additionally splits kwargs against a NULL-terminated kwlist of param
// names, giving an exposed callable Python's positional-or-keyword semantics.
int PyArg_ParseTuple(PyObject *args, const char *format, ...);
int PyArg_ParseTupleAndKeywords(PyObject *args, PyObject *kwargs,
                                const char *format, char **kwlist, ...);

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

// str marshalling. AsUTF8AndSize (above, shared with the BigInt hex path) reads
// a str's UTF-8 bytes -- it rejects non-str with TypeError and a surrogate-
// bearing str with UnicodeEncodeError (strict UTF-8). FromStringAndSize decodes
// a UTF-8 buffer back into a new str (UnicodeDecodeError on invalid UTF-8).
PyObject *PyUnicode_FromStringAndSize(const char *u, Py_ssize_t size);

// bytes marshalling. AsStringAndSize borrows a bytes object's buffer (TypeError
// for a non-bytes arg, including bytearray); FromStringAndSize copies a buffer
// into a new bytes object.
int PyBytes_AsStringAndSize(PyObject *obj, char **buffer, Py_ssize_t *length);
PyObject *PyBytes_FromStringAndSize(const char *v, Py_ssize_t len);

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

// Container marshalling (list/dict/set/tuple as @export param/return types).
// The marshal.hpp helpers are element-fn-parameterized and copy O(n) per
// element; admission is strict-by-container-kind via PyType_IsSubtype against
// these exported builtin type objects (the `*_Check` macros live in Python.h,
// which we deliberately do not include). PyAnySet (set | frozenset) reuses the
// two set type objects. The type objects are stable-ABI data symbols resolved
// against the host interpreter at import time.
extern PyTypeObject PyList_Type;
extern PyTypeObject PyDict_Type;
extern PyTypeObject PySet_Type;
extern PyTypeObject PyFrozenSet_Type;
extern PyTypeObject PyTuple_Type;

// IN accessors. List/dict/tuple yield BORROWED element refs (no DecRef);
// PyDict_Next walks in insertion order (matching ordered_map). A set has no
// stable-ABI indexed access, so set_from_py iterates via the iterator protocol
// (PyObject_GetIter once, then PyIter_Next per element -- each a NEW ref to
// DecRef; returns NULL at exhaustion or on error).
Py_ssize_t PyList_Size(PyObject *list);
PyObject *PyList_GetItem(PyObject *list, Py_ssize_t index);
int PyDict_Next(PyObject *dp, Py_ssize_t *pos, PyObject **key, PyObject **value);
Py_ssize_t PyTuple_Size(PyObject *tup);
PyObject *PyTuple_GetItem(PyObject *tup, Py_ssize_t index);
PyObject *PyObject_GetIter(PyObject *o);
PyObject *PyIter_Next(PyObject *o);

// OUT constructors. PyList_SetItem / PyTuple_SetItem STEAL the element ref;
// PyDict_SetItem / PySet_Add do NOT (the caller still owns and must DecRef).
PyObject *PyList_New(Py_ssize_t len);
int PyList_SetItem(PyObject *list, Py_ssize_t index, PyObject *item);
int PyDict_SetItem(PyObject *dp, PyObject *key, PyObject *item);
PyObject *PySet_New(PyObject *iterable);
int PySet_Add(PyObject *set, PyObject *key);
PyObject *PyTuple_New(Py_ssize_t len);
int PyTuple_SetItem(PyObject *tup, Py_ssize_t index, PyObject *item);

// Stable-ABI exception singletons (provided by the host interpreter). The
// exc_bridge cascade maps each tpy::BaseException subclass to its counterpart
// here, so the set mirrors the core.hpp taxonomy.
extern PyObject *PyExc_BaseException;
extern PyObject *PyExc_Exception;
extern PyObject *PyExc_ValueError;
extern PyObject *PyExc_OSError;
extern PyObject *PyExc_FileNotFoundError;
extern PyObject *PyExc_PermissionError;
extern PyObject *PyExc_BlockingIOError;
extern PyObject *PyExc_FileExistsError;
extern PyObject *PyExc_NotADirectoryError;
extern PyObject *PyExc_IsADirectoryError;
extern PyObject *PyExc_ConnectionError;
extern PyObject *PyExc_BrokenPipeError;
extern PyObject *PyExc_ConnectionResetError;
extern PyObject *PyExc_ConnectionRefusedError;
extern PyObject *PyExc_ConnectionAbortedError;
extern PyObject *PyExc_AttributeError;
extern PyObject *PyExc_AssertionError;
extern PyObject *PyExc_LookupError;
extern PyObject *PyExc_IndexError;
extern PyObject *PyExc_KeyError;
extern PyObject *PyExc_ArithmeticError;
extern PyObject *PyExc_ZeroDivisionError;
extern PyObject *PyExc_OverflowError;
extern PyObject *PyExc_FloatingPointError;
extern PyObject *PyExc_TypeError;
extern PyObject *PyExc_NotImplementedError;
extern PyObject *PyExc_RuntimeError;
extern PyObject *PyExc_RecursionError;
extern PyObject *PyExc_EOFError;
extern PyObject *PyExc_MemoryError;
extern PyObject *PyExc_StopIteration;
extern PyObject *PyExc_StopAsyncIteration;
extern PyObject *PyExc_TimeoutError;
extern PyObject *PyExc_GeneratorExit;
extern PyObject *PyExc_KeyboardInterrupt;

// The None singleton. Py_None is the macro `&_Py_NoneStruct`; we mirror the
// underlying data symbol so void-return wrappers can hand back a fresh ref.
extern PyObject _Py_NoneStruct;

}  // extern "C"

}  // namespace tpy::cpy
