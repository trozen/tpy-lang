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

#include <cstddef>
#include <cstdint>
#include <initializer_list>
#include <limits>
#include <string>
#include <string_view>
#include <tuple>
#include <type_traits>
#include <utility>
#include <vector>

#include "tpy/bigint.hpp"
#include "tpy/buffer_types.hpp"
#include "tpy/interop/cpython_h.hpp"
#include "tpy/ordered_map.hpp"
#include "tpy/ordered_set.hpp"

namespace tpy::interop {

// Thrown when a from_py conversion fails. Carries nothing: the Python
// exception is already set on the thread state, so the boundary catch just
// returns NULL.
struct MarshalError {};

// A REQUIRED keyword-only parameter cannot be spelled in a
// PyArg_ParseTupleAndKeywords format: `$` is only legal after `|`, so every
// slot from the keyword-only run onward parses as optional. The wrapper
// therefore lets the parser leave them null and reports the omission here,
// reproducing CPython's own wording verbatim (including the 3+ Oxford comma)
// so a caller sees the same text a `def` in Python would raise.
inline void require_kwonly(const char *fn_name, const char *const *names,
                           const bool *supplied, std::size_t n) {
    std::vector<const char *> missing;
    for (std::size_t i = 0; i < n; ++i) {
        if (!supplied[i]) {
            missing.push_back(names[i]);
        }
    }
    if (missing.empty()) {
        return;
    }
    std::string msg = std::string(fn_name) + "() missing "
                    + std::to_string(missing.size())
                    + " required keyword-only argument"
                    + (missing.size() == 1 ? "" : "s") + ": ";
    for (std::size_t i = 0; i < missing.size(); ++i) {
        if (i > 0) {
            msg += (i + 1 == missing.size())
                 ? (missing.size() == 2 ? " and " : ", and ")
                 : ", ";
        }
        msg += '\'';
        msg += missing[i];
        msg += '\'';
    }
    cpy::PyErr_SetString(cpy::PyExc_TypeError, msg.c_str());
    throw MarshalError{};
}

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
                             "Python int too large to convert to int64");
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
// long long. uint64 is the exception (its top half overflows long long) and has
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
inline ::tpy::Bytes from_py<::tpy::Bytes>(cpy::PyObject *o) {
    char *buf = nullptr;
    cpy::Py_ssize_t n = 0;
    if (cpy::PyBytes_AsStringAndSize(o, &buf, &n) < 0) {  // TypeError if not bytes
        throw MarshalError{};
    }
    const auto *p = reinterpret_cast<const std::uint8_t *>(buf);
    return ::tpy::Bytes(p, p + n);
}

// ---- Span[T] numeric marshalling (buffer protocol) -------------------------
//
// A Span[T]/Span[readonly[T]] @export param binds to any buffer-protocol
// exporter (bytes, bytearray, array.array, memoryview, numpy arrays): the data
// is copied into a fresh std::vector<T> (v1 is copy-in only, mirroring the
// container boundary -- there is no write-back), which implicitly converts to
// the function's std::span<T>/std::span<const T> param at the call site, the
// same "owned local outlives the call" trick str/bytes use for
// string_view/span<const uint8_t>.

namespace detail {
// True if `prefix` (one of '@'/'='/'<'/'>'/'!', or '\0' for "no prefix")
// denotes this host's native byte order. '@' (native size/alignment) and '='
// (native order, standard size) are native-order codes by definition (PEP
// 3118); '<'/'>'/'!' are explicit byte orders that match only when they
// happen to equal the host's -- accepting a mismatched one would silently
// byte-swap every element.
inline bool span_format_prefix_is_native(char prefix) {
#if defined(__BYTE_ORDER__) && __BYTE_ORDER__ == __ORDER_BIG_ENDIAN__
    constexpr bool host_little_endian = false;
#else
    constexpr bool host_little_endian = true;
#endif
    switch (prefix) {
        case '\0':
        case '@':
        case '=':
            return true;
        case '<':
            return host_little_endian;
        case '>':
        case '!':
            return !host_little_endian;
        default:
            return false;
    }
}

// The buffer's format string denotes a byte layout compatible with T (per the
// `struct`/`array` module typecodes). A leading byte-order/alignment
// character (@=<>!) is stripped first -- native exporters (array.array,
// numpy) always include one -- and rejected if it doesn't denote this host's
// native order. A null format means "unsigned bytes" (the buffer-protocol
// default for an exporter that ignores PyBUF_FORMAT).
inline bool span_format_matches(const char *format,
                                std::initializer_list<char> codes) {
    if (format == nullptr) {
        for (char c : codes) {
            if (c == 'B') return true;
        }
        return false;
    }
    const char *p = format;
    char prefix = '\0';
    if (*p == '@' || *p == '=' || *p == '<' || *p == '>' || *p == '!') {
        prefix = *p;
        ++p;
    }
    if (!span_format_prefix_is_native(prefix)) {
        return false;
    }
    if (p[0] == '\0' || p[1] != '\0') {
        return false;  // exactly one code character after the optional prefix
    }
    for (char c : codes) {
        if (*p == c) return true;
    }
    return false;
}

// Releases a Py_buffer view on scope exit (success, validation failure, or an
// exception from the result-vector allocation) -- without this, an
// std::bad_alloc thrown while copying out the data would leak the exporter's
// held reference and skip its releasebuffer hook.
struct BufferGuard {
    cpy::Py_buffer *view;
    ~BufferGuard() { cpy::PyBuffer_Release(view); }
};

// Shared buffer-read path: request a 1-D C-contiguous buffer (PyBUF_ND
// implies contiguity since no strides are requested) with its format string
// (PyBUF_FORMAT), validate itemsize + format against T and `codes`, and copy
// into a fresh vector.
template <class T>
inline std::vector<T> span_from_py_impl(cpy::PyObject *o,
                                        std::initializer_list<char> codes) {
    cpy::Py_buffer view;
    if (cpy::PyObject_GetBuffer(o, &view, cpy::PyBUF_ND | cpy::PyBUF_FORMAT) <
        0) {
        throw MarshalError{};  // PyObject_GetBuffer already set an exception
    }
    BufferGuard guard{&view};
    if (view.ndim != 1 ||
        view.itemsize != static_cast<cpy::Py_ssize_t>(sizeof(T)) ||
        !span_format_matches(view.format, codes)) {
        cpy::PyErr_SetString(cpy::PyExc_TypeError,
                             "buffer element type/shape does not match the "
                             "expected Span element type");
        throw MarshalError{};
    }
    const auto *p = static_cast<const T *>(view.buf);
    return std::vector<T>(p, p + view.len / view.itemsize);
}
}  // namespace detail

template <class T>
std::vector<T> span_from_py(cpy::PyObject *o);

template <>
inline std::vector<std::int8_t> span_from_py<std::int8_t>(cpy::PyObject *o) {
    return detail::span_from_py_impl<std::int8_t>(o, {'b'});
}
template <>
inline std::vector<std::uint8_t> span_from_py<std::uint8_t>(cpy::PyObject *o) {
    return detail::span_from_py_impl<std::uint8_t>(o, {'B'});
}
template <>
inline std::vector<std::int16_t> span_from_py<std::int16_t>(cpy::PyObject *o) {
    return detail::span_from_py_impl<std::int16_t>(o, {'h'});
}
template <>
inline std::vector<std::uint16_t> span_from_py<std::uint16_t>(
    cpy::PyObject *o) {
    return detail::span_from_py_impl<std::uint16_t>(o, {'H'});
}
template <>
inline std::vector<std::int32_t> span_from_py<std::int32_t>(cpy::PyObject *o) {
    // 'l' accepted alongside 'i': a native C long is 4 bytes on some
    // platforms (e.g. Windows), and the itemsize check above is the real
    // safety guard -- only the width that actually matches sizeof(T) passes.
    return detail::span_from_py_impl<std::int32_t>(o, {'i', 'l'});
}
template <>
inline std::vector<std::uint32_t> span_from_py<std::uint32_t>(
    cpy::PyObject *o) {
    return detail::span_from_py_impl<std::uint32_t>(o, {'I', 'L'});
}
template <>
inline std::vector<std::int64_t> span_from_py<std::int64_t>(cpy::PyObject *o) {
    // 'l' accepted alongside 'q': a native C long is 8 bytes on 64-bit
    // Linux/macOS (numpy's int64 buffers report 'l' there); itemsize gates it.
    return detail::span_from_py_impl<std::int64_t>(o, {'q', 'l'});
}
template <>
inline std::vector<std::uint64_t> span_from_py<std::uint64_t>(
    cpy::PyObject *o) {
    return detail::span_from_py_impl<std::uint64_t>(o, {'Q', 'L'});
}
template <>
inline std::vector<double> span_from_py<double>(cpy::PyObject *o) {
    return detail::span_from_py_impl<double>(o, {'d'});
}

// to_py: a TPy value -> a new owned PyObject reference.
inline cpy::PyObject *to_py(std::int64_t v) {
    return cpy::PyLong_FromLongLong(static_cast<long long>(v));
}

// The remaining fixed-width ints: signed and <= 32-bit-unsigned widen into the
// signed long long accessor; uint64 needs the unsigned one. Constrained so it
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

inline cpy::PyObject *to_py(std::string_view s) {
    return cpy::PyUnicode_FromStringAndSize(  // UnicodeDecodeError on bad UTF-8
        s.data(), static_cast<cpy::Py_ssize_t>(s.size()));
}

inline cpy::PyObject *to_py(const std::string &s) {
    return to_py(std::string_view(s));
}

// Disambiguates a string-literal / `const char*` arg: without it both the
// string_view and const std::string& overloads are viable via a user-defined
// conversion, making the call ambiguous.
inline cpy::PyObject *to_py(const char *s) {
    return to_py(std::string_view(s));
}

inline cpy::PyObject *to_py(const ::tpy::Bytes &b) {
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

// A richcompare/nb_* slot's "I don't know how to handle the other operand"
// return: a fresh ref to the NotImplemented singleton (Py_RETURN_NOTIMPLEMENTED).
inline cpy::PyObject *notimplemented_to_py() {
    cpy::Py_IncRef(&cpy::_Py_NotImplementedStruct);
    return &cpy::_Py_NotImplementedStruct;
}

// tp_hash must never return -1 (CPython reserves it as the error sentinel);
// remap to -2, the same idiom CPython's own hash wrappers use.
inline cpy::Py_ssize_t hash_to_py_hash_t(std::uint64_t h) {
    auto v = static_cast<cpy::Py_ssize_t>(h);
    return v == -1 ? -2 : v;
}

inline cpy::PyObject *to_py(const tpy::BigInt &b) {
    std::int64_t v;
    if (b.to_i64_checked(v)) {  // fast path
        return cpy::PyLong_FromLongLong(static_cast<long long>(v));
    }
    std::string hx = b.to_hex_string();  // "[-]0x..", parsed by PyLong_FromString
    return cpy::PyLong_FromString(hx.c_str(), nullptr, 16);
}

// ---- Container marshalling ------------------------------------------------
//
// list/dict/set/tuple cross the @export boundary O(n) by-copy. The codegen
// glue, which holds the unambiguous TPy element types, drives the recursion and
// passes a per-element conversion callable; these helpers own only the
// container-shaped traversal + refcounting. Keying off the C++ type alone is
// impossible for a container of containers -- so the leaf choice (e.g. the
// element callable for list[bytes] vs list[list[uint8]]) must come from the
// glue, never from a template specialization here.
//
// Direction mirrors the scalars: from_* THROWS MarshalError on failure (a
// Python exception is already set); to_* RETURNS nullptr on failure (matching
// to_py). So a recursive element callable composes on either side.

// list[T] <- PyList. Element refs are borrowed (PyList_GetItem).
template <class T, class Fn>
std::vector<T> list_from_py(cpy::PyObject *o, Fn elem) {
    if (cpy::PyType_IsSubtype(cpy::Py_TYPE(o), &cpy::PyList_Type) == 0) {
        cpy::PyErr_SetString(cpy::PyExc_TypeError, "expected a list");
        throw MarshalError{};
    }
    cpy::Py_ssize_t n = cpy::PyList_Size(o);
    std::vector<T> v;
    v.reserve(static_cast<std::size_t>(n));
    for (cpy::Py_ssize_t i = 0; i < n; ++i) {
        v.push_back(elem(cpy::PyList_GetItem(o, i)));  // borrowed
    }
    return v;
}

// dict[K, V] <- PyDict. Key/value refs are borrowed (PyDict_Next), walked in
// insertion order. Key converts before value (left-to-right, CPython arg order).
template <class K, class V, class KF, class VF>
tpy::ordered_map<K, V> dict_from_py(cpy::PyObject *o, KF kf, VF vf) {
    if (cpy::PyType_IsSubtype(cpy::Py_TYPE(o), &cpy::PyDict_Type) == 0) {
        cpy::PyErr_SetString(cpy::PyExc_TypeError, "expected a dict");
        throw MarshalError{};
    }
    tpy::ordered_map<K, V> m;
    cpy::PyObject *k = nullptr, *val = nullptr;
    cpy::Py_ssize_t pos = 0;
    while (cpy::PyDict_Next(o, &pos, &k, &val) != 0) {
        K kk = kf(k);
        m.insert_or_assign(std::move(kk), vf(val));
    }
    return m;
}

// set[T] <- PySet/PyFrozenSet. No stable-ABI indexed access, so iterate the
// iterator protocol; each PyIter_Next yields a NEW ref to release -- even when
// the element conversion throws mid-iteration.
template <class T, class Fn>
tpy::ordered_set<T> set_from_py(cpy::PyObject *o, Fn elem) {
    cpy::PyTypeObject *ty = cpy::Py_TYPE(o);
    if (cpy::PyType_IsSubtype(ty, &cpy::PySet_Type) == 0 &&
        cpy::PyType_IsSubtype(ty, &cpy::PyFrozenSet_Type) == 0) {
        cpy::PyErr_SetString(cpy::PyExc_TypeError, "expected a set");
        throw MarshalError{};
    }
    cpy::PyObject *it = cpy::PyObject_GetIter(o);
    if (it == nullptr) {
        throw MarshalError{};
    }
    tpy::ordered_set<T> s;
    cpy::PyObject *item = nullptr;
    while ((item = cpy::PyIter_Next(it)) != nullptr) {
        try {
            s.insert(elem(item));
        } catch (...) {
            cpy::Py_DecRef(item);
            cpy::Py_DecRef(it);
            throw;
        }
        cpy::Py_DecRef(item);
    }
    cpy::Py_DecRef(it);
    if (cpy::PyErr_Occurred() != nullptr) {  // PyIter_Next failed mid-iteration
        throw MarshalError{};
    }
    return s;
}

namespace detail {
// Braced-init guarantees left-to-right evaluation of the per-index conversions.
template <class Tuple, class FnTuple, std::size_t... I>
Tuple tuple_from_py_impl(cpy::PyObject *o, FnTuple &fns,
                         std::index_sequence<I...>) {
    return Tuple{std::get<I>(fns)(
        cpy::PyTuple_GetItem(o, static_cast<cpy::Py_ssize_t>(I)))...};  // borrowed
}
}  // namespace detail

// tuple[Ts...] <- PyTuple, fixed arity. Ts are the explicit element types; the
// glue supplies one conversion callable per element (Fns has the same length).
template <class... Ts, class... Fns>
std::tuple<Ts...> tuple_from_py(cpy::PyObject *o, Fns... fns) {
    if (cpy::PyType_IsSubtype(cpy::Py_TYPE(o), &cpy::PyTuple_Type) == 0) {
        cpy::PyErr_SetString(cpy::PyExc_TypeError, "expected a tuple");
        throw MarshalError{};
    }
    if (cpy::PyTuple_Size(o) != static_cast<cpy::Py_ssize_t>(sizeof...(Ts))) {
        cpy::PyErr_SetString(cpy::PyExc_TypeError,
                             "tuple has the wrong number of elements");
        throw MarshalError{};
    }
    auto fn_tuple = std::forward_as_tuple(fns...);
    return detail::tuple_from_py_impl<std::tuple<Ts...>>(
        o, fn_tuple, std::index_sequence_for<Fns...>{});
}

// std::vector<T> -> PyList. elem(e) yields a NEW ref (or nullptr); SetItem steals.
template <class T, class Fn>
cpy::PyObject *list_to_py(const std::vector<T> &v, Fn elem) {
    cpy::PyObject *lst = cpy::PyList_New(static_cast<cpy::Py_ssize_t>(v.size()));
    if (lst == nullptr) {
        return nullptr;
    }
    cpy::Py_ssize_t i = 0;
    for (const auto &e : v) {
        cpy::PyObject *pe = elem(e);
        if (pe == nullptr) {
            cpy::Py_DecRef(lst);
            return nullptr;
        }
        cpy::PyList_SetItem(lst, i++, pe);  // steals pe
    }
    return lst;
}

// tpy::ordered_map<K, V> -> PyDict (insertion order). SetItem does NOT steal.
template <class K, class V, class KF, class VF>
cpy::PyObject *dict_to_py(const tpy::ordered_map<K, V> &m, KF kf, VF vf) {
    cpy::PyObject *d = cpy::PyDict_New();
    if (d == nullptr) {
        return nullptr;
    }
    for (auto it = m.items_begin(); it != m.items_end(); ++it) {
        auto kv = *it;  // pair<const K&, const V&>
        cpy::PyObject *pk = kf(kv.first);
        if (pk == nullptr) {
            cpy::Py_DecRef(d);
            return nullptr;
        }
        cpy::PyObject *pv = vf(kv.second);
        if (pv == nullptr) {
            cpy::Py_DecRef(pk);
            cpy::Py_DecRef(d);
            return nullptr;
        }
        int rc = cpy::PyDict_SetItem(d, pk, pv);
        cpy::Py_DecRef(pk);
        cpy::Py_DecRef(pv);
        if (rc < 0) {
            cpy::Py_DecRef(d);
            return nullptr;
        }
    }
    return d;
}

// tpy::ordered_set<T> -> PySet (insertion order). Add does NOT steal.
template <class T, class Fn>
cpy::PyObject *set_to_py(const tpy::ordered_set<T> &s, Fn elem) {
    cpy::PyObject *ps = cpy::PySet_New(nullptr);
    if (ps == nullptr) {
        return nullptr;
    }
    for (const auto &e : s) {
        cpy::PyObject *pe = elem(e);
        if (pe == nullptr) {
            cpy::Py_DecRef(ps);
            return nullptr;
        }
        int rc = cpy::PySet_Add(ps, pe);
        cpy::Py_DecRef(pe);
        if (rc < 0) {
            cpy::Py_DecRef(ps);
            return nullptr;
        }
    }
    return ps;
}

namespace detail {
inline bool tuple_set_or_fail(cpy::PyObject *tup, cpy::Py_ssize_t i,
                              cpy::PyObject *pe) {
    if (pe == nullptr) {
        return false;
    }
    cpy::PyTuple_SetItem(tup, i, pe);  // steals pe
    return true;
}

template <class Tuple, class FnTuple, std::size_t... I>
cpy::PyObject *tuple_to_py_impl(const Tuple &t, FnTuple &fns,
                                std::index_sequence<I...>) {
    cpy::PyObject *tup = cpy::PyTuple_New(static_cast<cpy::Py_ssize_t>(sizeof...(I)));
    if (tup == nullptr) {
        return nullptr;
    }
    // Left-to-right; once one element fails, stop converting the rest. A
    // partially-filled tuple's unset slots are NULL, which Py_DecRef tolerates.
    bool ok = true;
    // `&&` short-circuits once ok is false, so a failed element skips every
    // later conversion (the call and its argument are never evaluated).
    ((ok = ok && tuple_set_or_fail(
          tup, static_cast<cpy::Py_ssize_t>(I),
          std::get<I>(fns)(std::get<I>(t)))),
     ...);
    if (!ok) {
        cpy::Py_DecRef(tup);
        return nullptr;
    }
    return tup;
}
}  // namespace detail

// std::tuple<Ts...> -> PyTuple. One conversion callable per element.
template <class... Ts, class... Fns>
cpy::PyObject *tuple_to_py(const std::tuple<Ts...> &t, Fns... fns) {
    auto fn_tuple = std::forward_as_tuple(fns...);
    return detail::tuple_to_py_impl(t, fn_tuple, std::index_sequence_for<Ts...>{});
}

}  // namespace tpy::interop
