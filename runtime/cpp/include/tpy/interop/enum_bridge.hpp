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

#include <type_traits>

#include "tpy/interop/cpython_h.hpp"
#include "tpy/interop/marshal.hpp"  // from_py / to_py + MarshalError (value crossing)

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

// Marshal a CPython enum member into the C++ `enum class E` (an @export param).
// The arg must be an instance of `enum_type` (the module's CPython enum) -- a
// bare int with the right value is rejected (TypeError), unlike a lenient `int`
// param: TPy must convert to a concrete enumerator, so the boundary is by-type.
// Reads `.value` (uniform for IntEnum and plain Enum, whose member is not itself
// an int) and casts the underlying integer.
template <class E>
inline E enum_from_py(cpy::PyObject *o, cpy::PyObject *enum_type) {
    if (cpy::PyObject_IsInstance(o, enum_type) != 1) {
        if (!cpy::PyErr_Occurred())
            cpy::PyErr_SetString(cpy::PyExc_TypeError,
                                 "expected an enum member of this type");
        throw MarshalError{};
    }
    cpy::PyObject *v = cpy::PyObject_GetAttrString(o, "value");
    if (!v) throw MarshalError{};
    // A member's `.value` is always an in-range int for a closed TPy enum, so
    // from_py cannot throw here -- hence no try/decref guard around it.
    auto raw = from_py<std::underlying_type_t<E>>(v);
    cpy::Py_DecRef(v);
    return static_cast<E>(raw);
}

// Marshal a C++ `enum class` value out as the corresponding CPython member:
// `EnumType(value)`. TPy enums are closed (no aliases, every value declared), so
// the lookup always resolves to a member -- never None/ValueError. Returns a new
// reference (or null with the error set if construction somehow fails). Templated
// on the enum's underlying integer type so a uint64 enum value above INT64_MAX
// crosses unsigned ("(K)") instead of wrapping to a negative `long long` ("(L)")
// -- which would build a value the closed enum has no member for.
template <class U>
inline cpy::PyObject *enum_to_py(cpy::PyObject *enum_type, U value) {
    cpy::PyObject *args;
    if constexpr (std::is_unsigned_v<U> && sizeof(U) == 8) {
        args = cpy::Py_BuildValue("(K)", static_cast<unsigned long long>(value));
    } else {
        args = cpy::Py_BuildValue("(L)", static_cast<long long>(value));
    }
    if (!args) return nullptr;
    cpy::PyObject *member = cpy::PyObject_Call(enum_type, args, nullptr);
    cpy::Py_DecRef(args);
    return member;
}

}  // namespace tpy::interop
