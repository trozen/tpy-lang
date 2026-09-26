/**
 * TurboPython Runtime - Bytes Operations
 *
 * Python-style bytes operations: printing (b'...'), encode/decode,
 * search, and manipulation helpers.
 */

#pragma once

#include <algorithm>
#include <cstdint>
#include <iostream>
#include <ranges>
#include <span>
#include <sstream>
#include <string>
#include <string_view>
#include <type_traits>
#include <vector>

#include "bigint.hpp"
#include "buffer_types.hpp"
#include "core.hpp"
#include "container_ops.hpp"
#include "ranges.hpp"

namespace tpy {

// `Bytes` and `ByteArray` are the two owning byte-buffer classes and
// `BytesView` the family's view (all three declared in buffer_types.hpp); the
// helpers below work in `BytesView` and return `Bytes`, the `bytes` storage
// form. A `bytearray`-returning method respells the result
// (`::tpy::ByteArray(...)`, a move) unless the helper is bytearray-only.

// -- Static bytes literal ---------------------------------------------------

inline BytesView bytes_literal(const char* data, size_t n) {
    return {reinterpret_cast<const uint8_t*>(data), n};
}

// -- Printing: b'hello\x00' ------------------------------------------------

namespace detail {

inline void write_byte_repr(std::ostream& os, uint8_t b) {
    if (b == '\\') {
        os << "\\\\";
    } else if (b == '\'') {
        os << "\\'";
    } else if (b == '\t') {
        os << "\\t";
    } else if (b == '\n') {
        os << "\\n";
    } else if (b == '\r') {
        os << "\\r";
    } else if (b >= 0x20 && b < 0x7f) {
        os << static_cast<char>(b);
    } else {
        os << "\\x";
        os << "0123456789abcdef"[b >> 4];
        os << "0123456789abcdef"[b & 0x0f];
    }
}

}  // namespace detail

// The printers take the bare vector too: a `list[uint8]` never prints as
// bytes, but the buffer-family helpers below hand them a plain vector, so
// the span is spelled rather than converted (`BytesView` refuses a bare
// vector by design).
struct BytesPrinter {
    BytesView value;
    explicit BytesPrinter(BytesView v) : value(v) {}
    explicit BytesPrinter(const std::vector<uint8_t>& v)
        : value(std::span<const uint8_t>(v)) {}
};

struct ByteArrayPrinter {
    BytesView value;
    explicit ByteArrayPrinter(BytesView v) : value(v) {}
    explicit ByteArrayPrinter(const std::vector<uint8_t>& v)
        : value(std::span<const uint8_t>(v)) {}
};

namespace detail {
inline void write_bytes_repr(std::ostream& os, BytesView data) {
    os << "b'";
    for (uint8_t b : data) {
        write_byte_repr(os, b);
    }
    os << '\'';
}
}  // namespace detail

inline std::ostream& operator<<(std::ostream& os, const BytesPrinter& bp) {
    detail::write_bytes_repr(os, bp.value);
    return os;
}

inline std::ostream& operator<<(std::ostream& os, const ByteArrayPrinter& bp) {
    os << "bytearray(";
    detail::write_bytes_repr(os, bp.value);
    os << ')';
    return os;
}

// __str__ / __repr__ overloads for raw Bytes / BytesView. Needed for
// generic dispatch (e.g. tpy::Any storing bytes), where the BytesPrinter
// wrapper isn't available at the call site.
inline std::string __str__(BytesView b) {
    std::ostringstream ss;
    ss << BytesPrinter{b};
    return ss.str();
}

inline std::string __str__(const Bytes& b) { return __str__(BytesView{b}); }
inline std::string __repr__(BytesView b) { return __str__(b); }
inline std::string __repr__(const Bytes& b) { return __str__(BytesView{b}); }

// A bytearray reprs as `bytearray(b'..')`. Its own type is what decides that,
// so no print-form tag has to be carried from the front end.
inline std::string __str__(const ByteArray& b) {
    std::ostringstream ss;
    ss << ByteArrayPrinter{BytesView{b}};
    return ss.str();
}
inline std::string __repr__(const ByteArray& b) { return __str__(b); }

// -- Construction -----------------------------------------------------------

inline Bytes bytes_from_size(int32_t n) {
    if (n < 0) raise_value_error("negative count");
    return Bytes(static_cast<size_t>(n), 0);
}

// Owned bytes from a raw byte buffer of `size` bytes (unsafe_bytes_from_buf).
// Caller ensures [p, p + size) is valid for read.
inline Bytes bytes_from_buf(const uint8_t* p, uint64_t size) {
    return Bytes(p, p + size);
}

inline uint8_t int_to_byte(int32_t v) {
    if (v < 0 || v > 255) raise_value_error("bytes must be in range(0, 256)");
    return static_cast<uint8_t>(v);
}

// Owned counterpart of bytes_literal: a bytes literal materialized into an
// owning vector (for owned slots -- fields, list elements, returns).
inline Bytes bytes_literal_owned(const char* data, size_t n) {
    return Bytes(bytes_literal(data, n));
}

// TODO(hot-path): per-element int_to_byte range check; revisit alongside a
// @not_hot_path / unchecked variant. Prefer the uint8 overload when possible.
// The buffer is deduced: the same helper fills a fresh `bytes` (through
// bytes_from_int_iterable) and appends to a live `bytearray`.
template<typename Buf, typename Arg>
void bytes_extend_int_iterable(Buf& self, Arg&& arg) {
    if constexpr (std::ranges::input_range<std::remove_cvref_t<Arg>>) {
        if constexpr (std::ranges::sized_range<std::remove_cvref_t<Arg>>) {
            self.reserve(self.size() + std::ranges::size(arg));
        }
        for (auto&& v : arg) self.push_back(int_to_byte(v));
    } else {
        auto&& __iter = ::tpy::iter_of(arg);
        for (;;) {
            auto __r = __iter.__next__();
            if (!__r.has_value()) break;
            self.push_back(int_to_byte(unwrap_ref(*__r)));
        }
    }
}

template<typename Arg>
Bytes bytes_from_int_iterable(Arg&& arg) {
    Bytes result;
    bytes_extend_int_iterable(result, std::forward<Arg>(arg));
    return result;
}

inline Bytes bytes_from_str(std::string_view s) {
    return Bytes(s.begin(), s.end());
}

// -- Conversion -------------------------------------------------------------

inline std::string bytes_decode(BytesView b) {
    return std::string(reinterpret_cast<const char*>(b.data()), b.size());
}

inline std::string bytes_hex(BytesView b) {
    std::string result;
    result.reserve(b.size() * 2);
    for (uint8_t byte : b) {
        result += "0123456789abcdef"[byte >> 4];
        result += "0123456789abcdef"[byte & 0x0f];
    }
    return result;
}

// -- Element access ---------------------------------------------------------

inline uint8_t bytes_getitem(BytesView b, int32_t index) {
    auto i = normalize_index(b, index, "bytes index out of range");
    return b[i];
}

// -- Search -----------------------------------------------------------------

inline int32_t bytes_find(BytesView haystack, BytesView needle) {
    if (needle.empty()) return 0;
    auto it = std::search(haystack.begin(), haystack.end(),
                          needle.begin(), needle.end());
    if (it == haystack.end()) return -1;
    return static_cast<int32_t>(it - haystack.begin());
}

inline int32_t bytes_rfind(BytesView haystack, BytesView needle) {
    if (needle.empty()) return static_cast<int32_t>(haystack.size());
    if (needle.size() > haystack.size()) return -1;
    for (auto i = static_cast<int32_t>(haystack.size() - needle.size()); i >= 0; --i) {
        if (std::equal(needle.begin(), needle.end(), haystack.begin() + i)) {
            return i;
        }
    }
    return -1;
}

inline int32_t bytes_count(BytesView haystack, BytesView needle) {
    if (needle.empty()) return static_cast<int32_t>(haystack.size()) + 1;
    if (needle.size() > haystack.size()) return 0;
    int32_t count = 0;
    size_t pos = 0;
    while (pos + needle.size() <= haystack.size()) {
        auto it = std::search(haystack.begin() + static_cast<std::ptrdiff_t>(pos), haystack.end(),
                              needle.begin(), needle.end());
        if (it == haystack.end()) break;
        ++count;
        pos = static_cast<size_t>(it - haystack.begin()) + needle.size();
    }
    return count;
}

inline bool bytes_startswith(BytesView b, BytesView prefix) {
    if (prefix.size() > b.size()) return false;
    return std::equal(prefix.begin(), prefix.end(), b.begin());
}

inline bool bytes_endswith(BytesView b, BytesView suffix) {
    if (suffix.size() > b.size()) return false;
    return std::equal(suffix.begin(), suffix.end(), b.end() - static_cast<std::ptrdiff_t>(suffix.size()));
}

inline bool bytes_contains(BytesView haystack, uint8_t needle) {
    return std::find(haystack.begin(), haystack.end(), needle) != haystack.end();
}

inline bool bytes_contains(BytesView haystack, int32_t needle) {
    return bytes_contains(haystack, int_to_byte(needle));
}

// A BigInt needle raises outside the byte domain like CPython's
// `2**40 in b"..."` (ValueError, not False -- unlike dict/set membership,
// where an out-of-range key is simply absent).
inline bool bytes_contains(BytesView haystack, const BigInt& needle) {
    int32_t v;
    if (!needle.to_fixed_try<int32_t>(v)) {
        raise_value_error("bytes must be in range(0, 256)");
    }
    return bytes_contains(haystack, int_to_byte(v));
}

// -- Manipulation -----------------------------------------------------------

inline Bytes bytes_replace(BytesView s, BytesView old_sub, BytesView new_sub) {
    if (old_sub.empty()) {
        // Insert new_sub before each byte and at the end
        Bytes result;
        result.reserve(s.size() + (s.size() + 1) * new_sub.size());
        for (uint8_t b : s) {
            result.insert(result.end(), new_sub.begin(), new_sub.end());
            result.push_back(b);
        }
        result.insert(result.end(), new_sub.begin(), new_sub.end());
        return result;
    }
    if (old_sub.size() > s.size()) return Bytes(s.begin(), s.end());
    Bytes result;
    size_t pos = 0;
    while (pos + old_sub.size() <= s.size()) {
        auto it = std::search(s.begin() + static_cast<std::ptrdiff_t>(pos), s.end(), old_sub.begin(), old_sub.end());
        result.insert(result.end(), s.begin() + static_cast<std::ptrdiff_t>(pos), it);
        if (it == s.end()) return result;
        result.insert(result.end(), new_sub.begin(), new_sub.end());
        pos = static_cast<size_t>(it - s.begin()) + old_sub.size();
    }
    result.insert(result.end(), s.begin() + static_cast<std::ptrdiff_t>(pos), s.end());
    return result;
}

inline std::vector<Bytes> bytes_split(BytesView s, BytesView sep) {
    if (sep.empty()) raise_value_error("empty separator");
    std::vector<Bytes> result;
    size_t pos = 0;
    while (pos + sep.size() <= s.size()) {
        auto it = std::search(s.begin() + static_cast<std::ptrdiff_t>(pos), s.end(), sep.begin(), sep.end());
        result.emplace_back(s.begin() + static_cast<std::ptrdiff_t>(pos), it);
        if (it == s.end()) return result;
        pos = static_cast<size_t>(it - s.begin()) + sep.size();
    }
    result.emplace_back(s.begin() + static_cast<std::ptrdiff_t>(pos), s.end());
    return result;
}

template <typename Container>
Bytes bytes_join(BytesView sep, const Container& items) {
    Bytes result;
    bool first = true;
    for (const auto& item : items) {
        if (!first) result.insert(result.end(), sep.begin(), sep.end());
        BytesView view(item);
        result.insert(result.end(), view.begin(), view.end());
        first = false;
    }
    return result;
}

inline bool is_ascii_whitespace(uint8_t b) {
    return b == 0x20 || b == 0x09 || b == 0x0a || b == 0x0d || b == 0x0b || b == 0x0c;
}

// View-returning variants (for BytesView methods)
inline BytesView bytes_strip_view(BytesView b) {
    auto start = b.begin();
    auto end = b.end();
    while (start != end && is_ascii_whitespace(*start)) ++start;
    while (end != start && is_ascii_whitespace(*(end - 1))) --end;
    return BytesView(start, end);
}

inline BytesView bytes_lstrip_view(BytesView b) {
    auto start = b.begin();
    while (start != b.end() && is_ascii_whitespace(*start)) ++start;
    return BytesView(start, b.end());
}

inline BytesView bytes_rstrip_view(BytesView b) {
    auto end = b.end();
    while (end != b.begin() && is_ascii_whitespace(*(end - 1))) --end;
    return BytesView(b.begin(), end);
}

inline BytesView bytes_rstrip_chars_view(BytesView b, BytesView chars) {
    auto end = b.end();
    while (end != b.begin() &&
           std::find(chars.begin(), chars.end(), *(end - 1)) != chars.end())
        --end;
    return BytesView(b.begin(), end);
}

// Copy-returning variants. Only `bytearray`'s strip family copies: `bytes`
// (immutable) returns a view of the receiver instead.
inline ByteArray bytes_strip(BytesView b) {
    auto v = bytes_strip_view(b);
    return ByteArray(v.begin(), v.end());
}

inline ByteArray bytes_lstrip(BytesView b) {
    auto v = bytes_lstrip_view(b);
    return ByteArray(v.begin(), v.end());
}

inline ByteArray bytes_rstrip(BytesView b) {
    auto v = bytes_rstrip_view(b);
    return ByteArray(v.begin(), v.end());
}

inline ByteArray bytes_rstrip_chars(BytesView b, BytesView chars) {
    auto v = bytes_rstrip_chars_view(b, chars);
    return ByteArray(v.begin(), v.end());
}

inline Bytes bytes_upper(BytesView b) {
    Bytes result(b.begin(), b.end());
    for (auto& c : result) {
        if (c >= 'a' && c <= 'z') c -= 32;
    }
    return result;
}

inline bool bytes_contains_sub(BytesView haystack, BytesView needle) {
    return std::search(haystack.begin(), haystack.end(),
                       needle.begin(), needle.end()) != haystack.end();
}

inline Bytes bytes_read_sub(BytesView data, int32_t offset, int32_t count) {
    return Bytes(data.begin() + offset, data.begin() + offset + count);
}

// -- Concatenation / repetition ---------------------------------------------

inline Bytes bytes_concat(BytesView a, BytesView b) {
    Bytes result;
    result.reserve(a.size() + b.size());
    result.insert(result.end(), a.begin(), a.end());
    result.insert(result.end(), b.begin(), b.end());
    return result;
}

inline Bytes bytes_repeat(BytesView b, int32_t n) {
    if (n <= 0) return {};
    Bytes result;
    result.reserve(b.size() * static_cast<size_t>(n));
    for (int32_t i = 0; i < n; ++i) {
        result.insert(result.end(), b.begin(), b.end());
    }
    return result;
}

// -- Slicing ----------------------------------------------------------------

inline BytesView bytes_slice(BytesView b, int32_t start, int32_t stop) {
    auto sz = static_cast<int32_t>(b.size());
    if (start < 0) start = std::max(0, sz + start);
    if (stop < 0) stop = std::max(0, sz + stop);
    start = std::min(start, sz);
    stop = std::min(stop, sz);
    if (start >= stop) return {};
    return b.subspan(static_cast<std::size_t>(start),
                     static_cast<std::size_t>(stop - start));
}

inline Bytes bytes_stepped_slice(BytesView b, int32_t start, int32_t stop, int32_t step) {
    auto len = static_cast<std::ptrdiff_t>(b.size());
    auto [i, j, st] = detail::resolve_stepped_bounds(start, stop, step, len);
    Bytes result;
    if (st > 0) {
        for (auto k = i; k < j; k += st)
            result.push_back(b[static_cast<std::size_t>(k)]);
    } else {
        for (auto k = i; k > j; k += st)
            result.push_back(b[static_cast<std::size_t>(k)]);
    }
    return result;
}

/// BasicSlice/Slice overloads.
inline BytesView bytes_slice(BytesView b, BasicSlice sl) {
    return bytes_slice(b, sl.start.value_or(0), sl.stop.value_or(SLICE_END));
}

inline Bytes bytes_stepped_slice(BytesView b, Slice sl) {
    return bytes_stepped_slice(b, sl.start.value_or(SLICE_NONE),
                               sl.stop.value_or(SLICE_NONE),
                               sl.step.value_or(1));
}

// -- bytearray-returning siblings -------------------------------------------
//
// A `bytearray` method declares a `bytearray` result, and `ByteArray` does not
// convert from the `Bytes` its bytes-family sibling returns (the two types are
// deliberately unrelated -- see buffer_types.hpp). Each of these adopts the
// fresh buffer the sibling built: the explicit base-rvalue constructor moves it,
// so the respelling costs nothing.

inline ByteArray bytearray_from_size(int32_t n) {
    return ByteArray(bytes_from_size(n));
}

template<typename Arg>
ByteArray bytearray_from_int_iterable(Arg&& arg) {
    return ByteArray(bytes_from_int_iterable(std::forward<Arg>(arg)));
}

inline ByteArray bytearray_concat(BytesView a, BytesView b) {
    return ByteArray(bytes_concat(a, b));
}

inline ByteArray bytearray_repeat(BytesView b, int32_t n) {
    return ByteArray(bytes_repeat(b, n));
}

inline ByteArray bytearray_upper(BytesView b) {
    return ByteArray(bytes_upper(b));
}

// -- Mutation helpers (for bytearray) ---------------------------------------

inline void bytearray_setitem(ByteArray& b, int32_t index, uint8_t value) {
    auto i = normalize_index(b, index, "bytearray index out of range");
    b[i] = value;
}

inline void bytearray_setitem(ByteArray& b, int32_t index, int32_t value) {
    bytearray_setitem(b, index, int_to_byte(value));
}

inline uint8_t bytearray_pop(ByteArray& b) {
    if (b.empty()) raise_index_error("pop from empty bytearray");
    uint8_t val = b.back();
    b.pop_back();
    return val;
}

inline uint8_t bytearray_pop_at(ByteArray& b, int32_t index) {
    auto i = normalize_index(b, index, "pop index out of range");
    uint8_t val = b[i];
    b.erase(b.begin() + static_cast<std::ptrdiff_t>(i));
    return val;
}

inline void bytearray_insert(ByteArray& b, int32_t index, uint8_t value) {
    auto sz = static_cast<int32_t>(b.size());
    int32_t i = index;
    if (i < 0) i = std::max(0, sz + i);
    if (i > sz) i = sz;
    b.insert(b.begin() + i, value);
}

inline void bytearray_insert(ByteArray& b, int32_t index, int32_t value) {
    bytearray_insert(b, index, int_to_byte(value));
}

inline void bytearray_remove(ByteArray& b, uint8_t value) {
    auto it = std::find(b.begin(), b.end(), value);
    if (it == b.end()) raise_value_error("value not found in bytearray");
    b.erase(it);
}

inline void bytearray_remove(ByteArray& b, int32_t value) {
    bytearray_remove(b, int_to_byte(value));
}

}  // namespace tpy
