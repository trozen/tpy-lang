#pragma once
// Runtime support for exposing a TPy enum as a CPython enum.
//
// A TPy enum compiles to a plain `enum class Name : underlying`, so it has no
// CPython presence on its own. To make `mymod.Color` import as a real Python
// enum (so `.name`/`.value`/identity/iteration/lookup all match a source-level
// `class Color(IntEnum)`), the generated PyInit_ rebuilds it via the stdlib
// `enum` module's functional API at module-init time. Passing `module=` pins
// __module__/__qualname__ so the constructed type is observably identical to a
// class-statement enum (CPython-parity-verified).
//
// The generated glue fills a {name: value} dict (member values marshalled with
// the enum's underlying-int to_py) via enum_dict_add, then make_enum imports
// `enum`, picks IntEnum vs Enum, and calls it. Every path leaves the Python
// error state set on failure and never leaks a reference.

#include "tpy/interop/cpython_h.hpp"

namespace tpy::interop {

// Add one member to the enum's value dict, consuming `value` (a new ref from
// to_py, or nullptr if that marshalling already failed and set an error).
// Returns 0 on success, -1 on failure -- the caller short-circuits the rest of
// the members, so an unbuilt member's to_py is never evaluated.
inline int enum_dict_add(cpy::PyObject *dict, const char *name,
                         cpy::PyObject *value) {
    if (!value) return -1;  // to_py failed; its exception is already set
    int rc = cpy::PyDict_SetItemString(dict, name, value);
    cpy::Py_DecRef(value);  // SetItemString does not steal the value
    return rc;
}

// Recreate a TPy enum as a CPython IntEnum/Enum: `enum.IntEnum(name, members,
// module=module)`. `members` is borrowed (the caller owns and decrefs it).
// Returns a new reference to the type, or nullptr with a Python error set.
inline cpy::PyObject *make_enum(const char *name, const char *module,
                               bool is_int_enum, cpy::PyObject *members) {
    cpy::PyObject *enum_mod = cpy::PyImport_ImportModule("enum");
    if (!enum_mod) return nullptr;
    cpy::PyObject *base = cpy::PyObject_GetAttrString(
        enum_mod, is_int_enum ? "IntEnum" : "Enum");
    cpy::Py_DecRef(enum_mod);
    if (!base) return nullptr;
    // "O" adds its own ref to `members`, so decref'ing args below leaves the
    // caller's reference intact.
    cpy::PyObject *args = cpy::Py_BuildValue("(sO)", name, members);
    cpy::PyObject *kwargs = cpy::Py_BuildValue("{s:s}", "module", module);
    cpy::PyObject *cls = nullptr;
    if (args && kwargs) cls = cpy::PyObject_Call(base, args, kwargs);
    if (args) cpy::Py_DecRef(args);
    if (kwargs) cpy::Py_DecRef(kwargs);
    cpy::Py_DecRef(base);
    return cls;
}

}  // namespace tpy::interop
