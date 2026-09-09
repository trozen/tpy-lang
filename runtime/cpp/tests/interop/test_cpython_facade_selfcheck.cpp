// Layout + constant guard for the hand-mirrored CPython limited-API facade
// (tpy/interop/cpython_abi.hpp). Includes real <Python.h> alongside our mirror
// and static_asserts that every mirrored struct's size/offsets and every
// hardcoded constant match the real 3.12 stable ABI. A hand-mirroring slip
// becomes a compile error here instead of silent ABI corruption in a shipped
// .so.
//
// Compiled as a build-time test ONLY (needs Python dev headers); never linked
// into an extension .so. Build, e.g.:
//   g++ -std=c++23 -DPy_LIMITED_API=0x030c0000 \
//       -I runtime/cpp/include -I "$(python -c 'import sysconfig;print(sysconfig.get_path("include"))')" \
//       -c runtime/cpp/tests/interop/test_cpython_facade_selfcheck.cpp -o /dev/null

#define Py_LIMITED_API 0x030c0000
#include <Python.h>

#include <cstddef>

// Python.h #defines METH_*/PYTHON_API_VERSION as macros that would collide
// with the facade's same-named constexprs. Capture the real values into plain
// names, then #undef so cpython_abi.hpp's constants can be checked against
// them below.
namespace real_abi {
constexpr int meth_varargs = METH_VARARGS;
constexpr int meth_keywords = METH_KEYWORDS;
constexpr int meth_noargs = METH_NOARGS;
constexpr int python_api_version = PYTHON_API_VERSION;
constexpr int tp_dealloc = Py_tp_dealloc;
constexpr int tp_doc = Py_tp_doc;
constexpr int tp_hash = Py_tp_hash;
constexpr int tp_init = Py_tp_init;
constexpr int tp_methods = Py_tp_methods;
constexpr int tp_new = Py_tp_new;
constexpr int tp_repr = Py_tp_repr;
constexpr int tp_richcompare = Py_tp_richcompare;
constexpr int tp_str = Py_tp_str;
constexpr int tp_getset = Py_tp_getset;
constexpr int tp_free = Py_tp_free;
constexpr unsigned long tpflags_default = Py_TPFLAGS_DEFAULT;
constexpr unsigned long tpflags_basetype = Py_TPFLAGS_BASETYPE;
constexpr int pybuf_nd = PyBUF_ND;
constexpr int pybuf_format = PyBUF_FORMAT;
constexpr int py_lt = Py_LT;
constexpr int py_le = Py_LE;
constexpr int py_eq = Py_EQ;
constexpr int py_ne = Py_NE;
constexpr int py_gt = Py_GT;
constexpr int py_ge = Py_GE;
constexpr int nb_add = Py_nb_add;
constexpr int nb_and = Py_nb_and;
constexpr int nb_floor_divide = Py_nb_floor_divide;
constexpr int nb_inplace_add = Py_nb_inplace_add;
constexpr int nb_inplace_and = Py_nb_inplace_and;
constexpr int nb_inplace_floor_divide = Py_nb_inplace_floor_divide;
constexpr int nb_inplace_lshift = Py_nb_inplace_lshift;
constexpr int nb_inplace_multiply = Py_nb_inplace_multiply;
constexpr int nb_inplace_or = Py_nb_inplace_or;
constexpr int nb_inplace_remainder = Py_nb_inplace_remainder;
constexpr int nb_inplace_rshift = Py_nb_inplace_rshift;
constexpr int nb_inplace_subtract = Py_nb_inplace_subtract;
constexpr int nb_inplace_true_divide = Py_nb_inplace_true_divide;
constexpr int nb_inplace_xor = Py_nb_inplace_xor;
constexpr int nb_invert = Py_nb_invert;
constexpr int nb_lshift = Py_nb_lshift;
constexpr int nb_multiply = Py_nb_multiply;
constexpr int nb_negative = Py_nb_negative;
constexpr int nb_or = Py_nb_or;
constexpr int nb_positive = Py_nb_positive;
constexpr int nb_power = Py_nb_power;
constexpr int nb_remainder = Py_nb_remainder;
constexpr int nb_rshift = Py_nb_rshift;
constexpr int nb_subtract = Py_nb_subtract;
constexpr int nb_true_divide = Py_nb_true_divide;
constexpr int nb_xor = Py_nb_xor;
constexpr int mp_ass_subscript = Py_mp_ass_subscript;
constexpr int mp_length = Py_mp_length;
constexpr int mp_subscript = Py_mp_subscript;
constexpr int sq_contains = Py_sq_contains;
constexpr int sq_length = Py_sq_length;
constexpr int tp_iter = Py_tp_iter;
constexpr int tp_iternext = Py_tp_iternext;
}  // namespace real_abi
#undef METH_VARARGS
#undef METH_KEYWORDS
#undef METH_NOARGS
#undef PYTHON_API_VERSION
#undef Py_tp_dealloc
#undef Py_tp_doc
#undef Py_tp_hash
#undef Py_tp_init
#undef Py_tp_methods
#undef Py_tp_new
#undef Py_tp_repr
#undef Py_tp_richcompare
#undef Py_tp_str
#undef Py_tp_getset
#undef Py_tp_free
#undef Py_TPFLAGS_DEFAULT
#undef Py_TPFLAGS_BASETYPE
#undef PyBUF_ND
#undef PyBUF_FORMAT
#undef Py_LT
#undef Py_LE
#undef Py_EQ
#undef Py_NE
#undef Py_GT
#undef Py_GE
#undef Py_nb_add
#undef Py_nb_and
#undef Py_nb_floor_divide
#undef Py_nb_inplace_add
#undef Py_nb_inplace_and
#undef Py_nb_inplace_floor_divide
#undef Py_nb_inplace_lshift
#undef Py_nb_inplace_multiply
#undef Py_nb_inplace_or
#undef Py_nb_inplace_remainder
#undef Py_nb_inplace_rshift
#undef Py_nb_inplace_subtract
#undef Py_nb_inplace_true_divide
#undef Py_nb_inplace_xor
#undef Py_nb_invert
#undef Py_nb_lshift
#undef Py_nb_multiply
#undef Py_nb_negative
#undef Py_nb_or
#undef Py_nb_positive
#undef Py_nb_power
#undef Py_nb_remainder
#undef Py_nb_rshift
#undef Py_nb_subtract
#undef Py_nb_true_divide
#undef Py_nb_xor
#undef Py_mp_ass_subscript
#undef Py_mp_length
#undef Py_mp_subscript
#undef Py_sq_contains
#undef Py_sq_length
#undef Py_tp_iter
#undef Py_tp_iternext

#include "tpy/interop/cpython_abi.hpp"

// Struct layouts (namespaced facade types vs Python.h's global ones).
static_assert(sizeof(tpy::cpy::PyObject) == sizeof(::PyObject),
              "PyObject size mismatch");

static_assert(sizeof(tpy::cpy::PyMethodDef) == sizeof(::PyMethodDef),
              "PyMethodDef size mismatch");
static_assert(offsetof(tpy::cpy::PyMethodDef, ml_meth) ==
                  offsetof(::PyMethodDef, ml_meth),
              "PyMethodDef.ml_meth offset mismatch");
static_assert(offsetof(tpy::cpy::PyMethodDef, ml_flags) ==
                  offsetof(::PyMethodDef, ml_flags),
              "PyMethodDef.ml_flags offset mismatch");

static_assert(sizeof(tpy::cpy::PyModuleDef_Base) == sizeof(::PyModuleDef_Base),
              "PyModuleDef_Base size mismatch");

static_assert(sizeof(tpy::cpy::PyModuleDef) == sizeof(::PyModuleDef),
              "PyModuleDef size mismatch");
static_assert(offsetof(tpy::cpy::PyModuleDef, m_name) ==
                  offsetof(::PyModuleDef, m_name),
              "PyModuleDef.m_name offset mismatch");
static_assert(offsetof(tpy::cpy::PyModuleDef, m_methods) ==
                  offsetof(::PyModuleDef, m_methods),
              "PyModuleDef.m_methods offset mismatch");
static_assert(offsetof(tpy::cpy::PyModuleDef, m_size) ==
                  offsetof(::PyModuleDef, m_size),
              "PyModuleDef.m_size offset mismatch");

// Heap-type creation structs (exposed classes).
static_assert(sizeof(tpy::cpy::PyType_Slot) == sizeof(::PyType_Slot),
              "PyType_Slot size mismatch");
static_assert(offsetof(tpy::cpy::PyType_Slot, pfunc) ==
                  offsetof(::PyType_Slot, pfunc),
              "PyType_Slot.pfunc offset mismatch");

static_assert(sizeof(tpy::cpy::PyType_Spec) == sizeof(::PyType_Spec),
              "PyType_Spec size mismatch");
static_assert(offsetof(tpy::cpy::PyType_Spec, basicsize) ==
                  offsetof(::PyType_Spec, basicsize),
              "PyType_Spec.basicsize offset mismatch");
static_assert(offsetof(tpy::cpy::PyType_Spec, flags) ==
                  offsetof(::PyType_Spec, flags),
              "PyType_Spec.flags offset mismatch");
static_assert(offsetof(tpy::cpy::PyType_Spec, slots) ==
                  offsetof(::PyType_Spec, slots),
              "PyType_Spec.slots offset mismatch");

static_assert(sizeof(tpy::cpy::PyGetSetDef) == sizeof(::PyGetSetDef),
              "PyGetSetDef size mismatch");
static_assert(offsetof(tpy::cpy::PyGetSetDef, set) ==
                  offsetof(::PyGetSetDef, set),
              "PyGetSetDef.set offset mismatch");
static_assert(offsetof(tpy::cpy::PyGetSetDef, closure) ==
                  offsetof(::PyGetSetDef, closure),
              "PyGetSetDef.closure offset mismatch");

// Facade constants vs the real ABI values.
static_assert(tpy::cpy::METH_VARARGS == real_abi::meth_varargs,
              "METH_VARARGS value mismatch");
static_assert(tpy::cpy::METH_KEYWORDS == real_abi::meth_keywords,
              "METH_KEYWORDS value mismatch");
static_assert(tpy::cpy::METH_NOARGS == real_abi::meth_noargs,
              "METH_NOARGS value mismatch");
static_assert(tpy::cpy::PYTHON_API_VERSION == real_abi::python_api_version,
              "PYTHON_API_VERSION value mismatch");
static_assert(tpy::cpy::Py_tp_dealloc == real_abi::tp_dealloc,
              "Py_tp_dealloc value mismatch");
static_assert(tpy::cpy::Py_tp_doc == real_abi::tp_doc,
              "Py_tp_doc value mismatch");
static_assert(tpy::cpy::Py_tp_hash == real_abi::tp_hash,
              "Py_tp_hash value mismatch");
static_assert(tpy::cpy::Py_tp_init == real_abi::tp_init,
              "Py_tp_init value mismatch");
static_assert(tpy::cpy::Py_tp_methods == real_abi::tp_methods,
              "Py_tp_methods value mismatch");
static_assert(tpy::cpy::Py_tp_new == real_abi::tp_new,
              "Py_tp_new value mismatch");
static_assert(tpy::cpy::Py_tp_repr == real_abi::tp_repr,
              "Py_tp_repr value mismatch");
static_assert(tpy::cpy::Py_tp_richcompare == real_abi::tp_richcompare,
              "Py_tp_richcompare value mismatch");
static_assert(tpy::cpy::Py_tp_str == real_abi::tp_str,
              "Py_tp_str value mismatch");
static_assert(tpy::cpy::Py_tp_getset == real_abi::tp_getset,
              "Py_tp_getset value mismatch");
static_assert(tpy::cpy::Py_tp_free == real_abi::tp_free,
              "Py_tp_free value mismatch");
static_assert(tpy::cpy::Py_TPFLAGS_BASETYPE == real_abi::tpflags_basetype,
              "Py_TPFLAGS_BASETYPE mismatch");
static_assert(tpy::cpy::Py_TPFLAGS_DEFAULT == real_abi::tpflags_default,
              "Py_TPFLAGS_DEFAULT value mismatch");
static_assert(tpy::cpy::Py_LT == real_abi::py_lt, "Py_LT value mismatch");
static_assert(tpy::cpy::Py_LE == real_abi::py_le, "Py_LE value mismatch");
static_assert(tpy::cpy::Py_EQ == real_abi::py_eq, "Py_EQ value mismatch");
static_assert(tpy::cpy::Py_NE == real_abi::py_ne, "Py_NE value mismatch");
static_assert(tpy::cpy::Py_GT == real_abi::py_gt, "Py_GT value mismatch");
static_assert(tpy::cpy::Py_GE == real_abi::py_ge, "Py_GE value mismatch");

// Arithmetic/ordering operator slot ids (Q4 checkpoint 2).
static_assert(tpy::cpy::Py_nb_add == real_abi::nb_add, "Py_nb_add value mismatch");
static_assert(tpy::cpy::Py_nb_and == real_abi::nb_and, "Py_nb_and value mismatch");
static_assert(tpy::cpy::Py_nb_floor_divide == real_abi::nb_floor_divide, "Py_nb_floor_divide value mismatch");
static_assert(tpy::cpy::Py_nb_inplace_add == real_abi::nb_inplace_add, "Py_nb_inplace_add value mismatch");
static_assert(tpy::cpy::Py_nb_inplace_and == real_abi::nb_inplace_and, "Py_nb_inplace_and value mismatch");
static_assert(tpy::cpy::Py_nb_inplace_floor_divide == real_abi::nb_inplace_floor_divide, "Py_nb_inplace_floor_divide value mismatch");
static_assert(tpy::cpy::Py_nb_inplace_lshift == real_abi::nb_inplace_lshift, "Py_nb_inplace_lshift value mismatch");
static_assert(tpy::cpy::Py_nb_inplace_multiply == real_abi::nb_inplace_multiply, "Py_nb_inplace_multiply value mismatch");
static_assert(tpy::cpy::Py_nb_inplace_or == real_abi::nb_inplace_or, "Py_nb_inplace_or value mismatch");
static_assert(tpy::cpy::Py_nb_inplace_remainder == real_abi::nb_inplace_remainder, "Py_nb_inplace_remainder value mismatch");
static_assert(tpy::cpy::Py_nb_inplace_rshift == real_abi::nb_inplace_rshift, "Py_nb_inplace_rshift value mismatch");
static_assert(tpy::cpy::Py_nb_inplace_subtract == real_abi::nb_inplace_subtract, "Py_nb_inplace_subtract value mismatch");
static_assert(tpy::cpy::Py_nb_inplace_true_divide == real_abi::nb_inplace_true_divide, "Py_nb_inplace_true_divide value mismatch");
static_assert(tpy::cpy::Py_nb_inplace_xor == real_abi::nb_inplace_xor, "Py_nb_inplace_xor value mismatch");
static_assert(tpy::cpy::Py_nb_invert == real_abi::nb_invert, "Py_nb_invert value mismatch");
static_assert(tpy::cpy::Py_nb_lshift == real_abi::nb_lshift, "Py_nb_lshift value mismatch");
static_assert(tpy::cpy::Py_nb_multiply == real_abi::nb_multiply, "Py_nb_multiply value mismatch");
static_assert(tpy::cpy::Py_nb_negative == real_abi::nb_negative, "Py_nb_negative value mismatch");
static_assert(tpy::cpy::Py_nb_or == real_abi::nb_or, "Py_nb_or value mismatch");
static_assert(tpy::cpy::Py_nb_positive == real_abi::nb_positive, "Py_nb_positive value mismatch");
static_assert(tpy::cpy::Py_nb_power == real_abi::nb_power, "Py_nb_power value mismatch");
static_assert(tpy::cpy::Py_nb_remainder == real_abi::nb_remainder, "Py_nb_remainder value mismatch");
static_assert(tpy::cpy::Py_nb_rshift == real_abi::nb_rshift, "Py_nb_rshift value mismatch");
static_assert(tpy::cpy::Py_nb_subtract == real_abi::nb_subtract, "Py_nb_subtract value mismatch");
static_assert(tpy::cpy::Py_nb_true_divide == real_abi::nb_true_divide, "Py_nb_true_divide value mismatch");
static_assert(tpy::cpy::Py_nb_xor == real_abi::nb_xor, "Py_nb_xor value mismatch");

// Container-protocol slot ids (Q4 checkpoint 3).
static_assert(tpy::cpy::Py_mp_ass_subscript == real_abi::mp_ass_subscript, "Py_mp_ass_subscript value mismatch");
static_assert(tpy::cpy::Py_mp_length == real_abi::mp_length, "Py_mp_length value mismatch");
static_assert(tpy::cpy::Py_mp_subscript == real_abi::mp_subscript, "Py_mp_subscript value mismatch");
static_assert(tpy::cpy::Py_sq_contains == real_abi::sq_contains, "Py_sq_contains value mismatch");
static_assert(tpy::cpy::Py_sq_length == real_abi::sq_length, "Py_sq_length value mismatch");
static_assert(tpy::cpy::Py_tp_iter == real_abi::tp_iter, "Py_tp_iter value mismatch");
static_assert(tpy::cpy::Py_tp_iternext == real_abi::tp_iternext, "Py_tp_iternext value mismatch");

// Buffer protocol (Span[T] numeric marshalling).
static_assert(sizeof(tpy::cpy::Py_buffer) == sizeof(::Py_buffer),
              "Py_buffer size mismatch");
static_assert(offsetof(tpy::cpy::Py_buffer, obj) == offsetof(::Py_buffer, obj),
              "Py_buffer.obj offset mismatch");
static_assert(offsetof(tpy::cpy::Py_buffer, len) == offsetof(::Py_buffer, len),
              "Py_buffer.len offset mismatch");
static_assert(offsetof(tpy::cpy::Py_buffer, itemsize) ==
                  offsetof(::Py_buffer, itemsize),
              "Py_buffer.itemsize offset mismatch");
static_assert(offsetof(tpy::cpy::Py_buffer, ndim) == offsetof(::Py_buffer, ndim),
              "Py_buffer.ndim offset mismatch");
static_assert(offsetof(tpy::cpy::Py_buffer, format) ==
                  offsetof(::Py_buffer, format),
              "Py_buffer.format offset mismatch");
static_assert(offsetof(tpy::cpy::Py_buffer, shape) ==
                  offsetof(::Py_buffer, shape),
              "Py_buffer.shape offset mismatch");
static_assert(tpy::cpy::PyBUF_ND == real_abi::pybuf_nd,
              "PyBUF_ND value mismatch");
static_assert(tpy::cpy::PyBUF_FORMAT == real_abi::pybuf_format,
              "PyBUF_FORMAT value mismatch");

int main() { return 0; }
