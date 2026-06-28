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
#include <limits>
#include <string>
#include <string_view>
#include <type_traits>
#include <vector>

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

// Shared narrow-int path: read a long long (an out-of-long-long value trips the
// overflow flag), then range-check against T's [min, max] -- covers every
// signed width and the unsigned widths <= 32 bits, all of which fit a signed
// long long. UInt64 is the exception (its top half overflows long long) and has
// its own specialization below.
template <class T>
inline T from_py_bounded(cpy::PyObject *o) {
    cpy::PyObject *idx = cpy::PyNumber_Index(o);  // accepts int + __index__
    if (idx == nullptr) {
        throw MarshalError{};
    }
    int overflow = 0;
    long long v = cpy::PyLong_AsLongLongAndOverflow(idx, &overflow);
    cpy::Py_DecRef(idx);
    // A non-overflow internal error returns -1 with a Python exception already
    // set; preserve it rather than reporting the value as in-range (mirrors the
    // from_py<int64_t> guard). Checked before the range test so the pending
    // exception is not masked by a spurious OverflowError for signed widths
    // where -1 is in range.
    if (overflow == 0 && v == -1 && cpy::PyErr_Occurred() != nullptr) {
        throw MarshalError{};
    }
    if (overflow != 0 ||
        v < static_cast<long long>(std::numeric_limits<T>::min()) ||
        v > static_cast<long long>(std::numeric_limits<T>::max())) {
        cpy::PyErr_SetString(cpy::PyExc_OverflowError,
                             "Python int out of range for the target int type");
        throw MarshalError{};
    }
    return static_cast<T>(v);
}

template <>
inline std::int8_t from_py<std::int8_t>(cpy::PyObject *o) {
    return from_py_bounded<std::int8_t>(o);
}
template <>
inline std::uint8_t from_py<std::uint8_t>(cpy::PyObject *o) {
    return from_py_bounded<std::uint8_t>(o);
}
template <>
inline std::int16_t from_py<std::int16_t>(cpy::PyObject *o) {
    return from_py_bounded<std::int16_t>(o);
}
template <>
inline std::uint16_t from_py<std::uint16_t>(cpy::PyObject *o) {
    return from_py_bounded<std::uint16_t>(o);
}
template <>
inline std::int32_t from_py<std::int32_t>(cpy::PyObject *o) {
    return from_py_bounded<std::int32_t>(o);
}
template <>
inline std::uint32_t from_py<std::uint32_t>(cpy::PyObject *o) {
    return from_py_bounded<std::uint32_t>(o);
}

template <>
inline std::uint64_t from_py<std::uint64_t>(cpy::PyObject *o) {
    cpy::PyObject *idx = cpy::PyNumber_Index(o);
    if (idx == nullptr) {
        throw MarshalError{};
    }
    unsigned long long v = cpy::PyLong_AsUnsignedLongLong(idx);
    cpy::Py_DecRef(idx);
    // CPython sets OverflowError for a negative or > UINT64_MAX value, returning
    // (unsigned long long)-1 as the sentinel.
    if (v == static_cast<unsigned long long>(-1) &&
        cpy::PyErr_Occurred() != nullptr) {
        throw MarshalError{};
    }
    return static_cast<std::uint64_t>(v);
}

template <>
inline bool from_py<bool>(cpy::PyObject *o) {
    int r = cpy::PyObject_IsTrue(o);  // truthiness coercion; -1 on __bool__ error
    if (r < 0) {
        throw MarshalError{};
    }
    return r != 0;
}

// Unlike the numeric marshallers, str/bytes do NOT coerce: a non-str / non-bytes
// argument is a TypeError, matching a C function that declared the exact type.
// The owned result (std::string / std::vector) backs the borrow-form param
// (string_view / span) the generated wrapper passes by implicit conversion, so
// the owned local must outlive the call (it does -- it lives in the wrapper).
template <>
inline std::string from_py<std::string>(cpy::PyObject *o) {
    cpy::Py_ssize_t n = 0;
    const char *s = cpy::PyUnicode_AsUTF8AndSize(o, &n);  // TypeError if not str
    if (s == nullptr) {
        throw MarshalError{};
    }
    return std::string(s, static_cast<std::size_t>(n));
}

template <>
inline std::vector<std::uint8_t> from_py<std::vector<std::uint8_t>>(
    cpy::PyObject *o) {
    char *buf = nullptr;
    cpy::Py_ssize_t n = 0;
    if (cpy::PyBytes_AsStringAndSize(o, &buf, &n) < 0) {  // TypeError if not bytes
        throw MarshalError{};
    }
    const auto *p = reinterpret_cast<const std::uint8_t *>(buf);
    return std::vector<std::uint8_t>(p, p + n);
}

// to_py: a TPy value -> a new owned PyObject reference.
inline cpy::PyObject *to_py(std::int64_t v) {
    return cpy::PyLong_FromLongLong(static_cast<long long>(v));
}

// The remaining fixed-width ints: signed and <= 32-bit-unsigned widen into the
// signed long long accessor; UInt64 needs the unsigned one. Constrained so it
// never competes with the int64_t / double overloads (an exact-match non-
// template wins) and never swallows bool.
template <class T>
    requires (std::is_integral_v<T> && !std::is_same_v<T, bool>)
inline cpy::PyObject *to_py(T v) {
    if constexpr (std::is_unsigned_v<T> && sizeof(T) == 8) {
        return cpy::PyLong_FromUnsignedLongLong(static_cast<unsigned long long>(v));
    } else {
        return cpy::PyLong_FromLongLong(static_cast<long long>(v));
    }
}

inline cpy::PyObject *to_py(double v) {
    return cpy::PyFloat_FromDouble(v);
}

inline cpy::PyObject *to_py(bool v) {
    return cpy::PyBool_FromLong(v ? 1 : 0);
}

inline cpy::PyObject *to_py(const std::string &s) {
    return cpy::PyUnicode_FromStringAndSize(  // UnicodeDecodeError on bad UTF-8
        s.data(), static_cast<cpy::Py_ssize_t>(s.size()));
}

inline cpy::PyObject *to_py(const std::vector<std::uint8_t> &b) {
    // b.data() may be null for an empty vector; PyBytes_FromStringAndSize(nullptr,
    // 0) takes CPython's uninitialized-buffer path rather than a 0-length copy.
    return cpy::PyBytes_FromStringAndSize(
        b.empty() ? "" : reinterpret_cast<const char *>(b.data()),
        static_cast<cpy::Py_ssize_t>(b.size()));
}

// A void @export returns None: a fresh ref to the None singleton (Py_RETURN_NONE).
inline cpy::PyObject *none_to_py() {
    cpy::Py_IncRef(&cpy::_Py_NoneStruct);
    return &cpy::_Py_NoneStruct;
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
