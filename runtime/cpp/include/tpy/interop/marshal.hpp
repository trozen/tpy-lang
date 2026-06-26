#pragma once
// Per-type CPython <-> TPy marshalling primitives (the C++ template layer).
//
// Codegen emits calls to these by C++ type name; overload/specialization picks
// the right one. Raw C-API calls go through the cpython_h.hpp facade, so no
// Python.h reaches this TU. This is the hot per-value path; the @native
// lib/tpy/_bindings/cpython.py bindings serve the TPy-written library side
// (PyRef etc.) and are a separate concern.
//
// Error model: a failing from_py sets a specific Python exception (TypeError /
// OverflowError) and throws MarshalError; the generated boundary catch returns
// the NULL sentinel, preserving the already-set exception.

#include <cstdint>
#include <string>
#include <string_view>

#include "tpy/bigint.hpp"
#include "tpy/interop/cpython_h.hpp"

namespace tpy::interop {

// Thrown when a from_py conversion fails. Carries nothing: the Python
// exception is already set on the thread state, so the boundary catch just
// returns NULL.
struct MarshalError {};

// from_py<T>: a borrowed PyObject -> a TPy value of type T.
template <class T>
T from_py(cpy::PyObject *o);

template <>
inline std::int64_t from_py<std::int64_t>(cpy::PyObject *o) {
    cpy::PyObject *idx = cpy::PyNumber_Index(o);  // accepts int + __index__
    if (idx == nullptr) {
        throw MarshalError{};  // PyNumber_Index set TypeError
    }
    int overflow = 0;
    long long v = cpy::PyLong_AsLongLongAndOverflow(idx, &overflow);
    cpy::Py_DecRef(idx);
    if (overflow != 0) {
        cpy::PyErr_SetString(cpy::PyExc_OverflowError,
                             "Python int too large to convert to Int64");
        throw MarshalError{};
    }
    if (v == -1 && cpy::PyErr_Occurred() != nullptr) {
        throw MarshalError{};
    }
    return static_cast<std::int64_t>(v);
}

template <>
inline tpy::BigInt from_py<tpy::BigInt>(cpy::PyObject *o) {
    cpy::PyObject *idx = cpy::PyNumber_Index(o);
    if (idx == nullptr) {
        throw MarshalError{};
    }
    int overflow = 0;
    long long v = cpy::PyLong_AsLongLongAndOverflow(idx, &overflow);
    if (overflow == 0) {
        bool err = (v == -1 && cpy::PyErr_Occurred() != nullptr);
        cpy::Py_DecRef(idx);
        if (err) {
            throw MarshalError{};
        }
        return tpy::BigInt(static_cast<std::int64_t>(v));  // fast path
    }
    // Genuine bignum: hex round-trip (decimal int<->str is capped by CPython's
    // int_max_str_digits; power-of-2 bases are exempt).
    cpy::PyObject *hex = cpy::PyNumber_ToBase(idx, 16);
    cpy::Py_DecRef(idx);
    if (hex == nullptr) {
        throw MarshalError{};
    }
    cpy::Py_ssize_t len = 0;
    const char *s = cpy::PyUnicode_AsUTF8AndSize(hex, &len);
    if (s == nullptr) {
        cpy::Py_DecRef(hex);
        throw MarshalError{};
    }
    // Copy the digits out and release the str BEFORE parsing: from_hex_str can
    // throw on malformed input, and `hex` must not leak on that path.
    std::string digits(s, static_cast<std::size_t>(len));
    cpy::Py_DecRef(hex);
    return tpy::BigInt::from_hex_str(digits);
}

template <>
inline double from_py<double>(cpy::PyObject *o) {
    double v = cpy::PyFloat_AsDouble(o);  // coerces via __float__ (int/bool too)
    if (v == -1.0 && cpy::PyErr_Occurred() != nullptr) {
        throw MarshalError{};
    }
    return v;
}

// to_py: a TPy value -> a new owned PyObject reference.
inline cpy::PyObject *to_py(std::int64_t v) {
    return cpy::PyLong_FromLongLong(static_cast<long long>(v));
}

inline cpy::PyObject *to_py(double v) {
    return cpy::PyFloat_FromDouble(v);
}

inline cpy::PyObject *to_py(const tpy::BigInt &b) {
    std::int64_t v;
    if (b.to_i64_checked(v)) {  // fast path
        return cpy::PyLong_FromLongLong(static_cast<long long>(v));
    }
    std::string hx = b.to_hex_string();  // "[-]0x..", parsed by PyLong_FromString
    return cpy::PyLong_FromString(hx.c_str(), nullptr, 16);
}

}  // namespace tpy::interop
