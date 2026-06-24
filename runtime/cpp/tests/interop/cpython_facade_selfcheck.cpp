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
//       -c runtime/cpp/tests/interop/cpython_facade_selfcheck.cpp -o /dev/null

#define Py_LIMITED_API 0x030c0000
#include <Python.h>

#include <cstddef>

// Python.h #defines METH_*/PYTHON_API_VERSION as macros that would collide
// with the facade's same-named constexprs. Capture the real values into plain
// names, then #undef so cpython_abi.hpp's constants can be checked against
// them below.
namespace real_abi {
constexpr int meth_varargs = METH_VARARGS;
constexpr int meth_noargs = METH_NOARGS;
constexpr int python_api_version = PYTHON_API_VERSION;
}  // namespace real_abi
#undef METH_VARARGS
#undef METH_NOARGS
#undef PYTHON_API_VERSION

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

// Facade constants vs the real ABI values.
static_assert(tpy::cpy::METH_VARARGS == real_abi::meth_varargs,
              "METH_VARARGS value mismatch");
static_assert(tpy::cpy::METH_NOARGS == real_abi::meth_noargs,
              "METH_NOARGS value mismatch");
static_assert(tpy::cpy::PYTHON_API_VERSION == real_abi::python_api_version,
              "PYTHON_API_VERSION value mismatch");

int main() { return 0; }
