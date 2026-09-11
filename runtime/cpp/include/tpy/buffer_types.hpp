/**
 * TurboPython Runtime - String / bytes buffer types
 *
 * TPy `String`, `bytes`, `bytearray` and `BytesView` each own a C++ type
 * here, so a trait keyed on the C++ type answers per TPy type: `std::string`
 * then means TPy `str` and nothing else, `std::vector<uint8_t>` means
 * `list[UInt8]` and nothing else, and `std::span<const uint8_t>` means
 * `Span[readonly[UInt8]]` and nothing else. Without that, one generic
 * instantiation serves two TPy types whose monomorphic twins take different
 * parameter forms, and the caller has to buy an owned temp to reach a slot
 * spelled for the other type.
 *
 * DERIVED, not wrapping: a wrapper reaches each of `__len__`'s
 * `const std::string&` and `std::string_view` overloads by one user-defined
 * conversion, so every such call is ambiguous. Deriving also keeps every
 * `const std::string&` / `std::string_view` / `const std::vector<uint8_t>&` /
 * `std::span<const uint8_t>` parameter in the runtime and in hand-written
 * `@native` code binding these types unchanged. Nothing is ever deleted
 * through a base pointer, so the non-virtual base destructors are safe.
 *
 * The cost of deriving is that a base overload found by TEMPLATE ARGUMENT
 * DEDUCTION (`__len__(const std::vector<T>&)`) no longer matches, while a
 * constrained template on the exact type does -- so the rows below and the
 * per-type overloads in dunder.hpp / printing.hpp / bytes_ops.hpp are what
 * keep the answers identical to the shared-type ones.
 */

#pragma once

#include <algorithm>
#include <compare>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <type_traits>
#include <functional>
#include <iterator>
#include <ranges>
#include <format>
#include <span>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

#include "type_traits.hpp"

namespace tpy {

// TPy `String` -- the mutable, owning sibling of `str`.
struct String : std::string {
    using std::string::string;
    String() = default;
    String(const std::string& s) : std::string(s) {}
    String(std::string&& s) : std::string(std::move(s)) {}
};

// TPy `bytes` -- immutable, so a value type; its parameter form is a view.
//
// Every buffer-taking constructor is EXPLICIT. A non-explicit one would let the
// two siblings convert into each other by a single user-defined conversion
// (derived-to-base on the argument, then the converting constructor), so
// `f(const Bytes&)` would silently accept and copy a `ByteArray` -- exactly the
// confusion the split exists to remove. The static_asserts below hold that.
struct Bytes : std::vector<std::uint8_t> {
    using std::vector<std::uint8_t>::vector;
    Bytes() = default;
    explicit Bytes(const std::vector<std::uint8_t>& v)
        : std::vector<std::uint8_t>(v) {}
    explicit Bytes(std::vector<std::uint8_t>&& v)
        : std::vector<std::uint8_t>(std::move(v)) {}
    explicit Bytes(std::span<const std::uint8_t> v)
        : std::vector<std::uint8_t>(v.begin(), v.end()) {}
};

// TPy `bytearray` -- a mutable buffer, hence a reference type: locals and
// field reads alias it rather than deep-copying, matching CPython.
struct ByteArray : std::vector<std::uint8_t> {
    using std::vector<std::uint8_t>::vector;
    ByteArray() = default;
    explicit ByteArray(const std::vector<std::uint8_t>& v)
        : std::vector<std::uint8_t>(v) {}
    explicit ByteArray(std::vector<std::uint8_t>&& v)
        : std::vector<std::uint8_t>(std::move(v)) {}
    explicit ByteArray(std::span<const std::uint8_t> v)
        : std::vector<std::uint8_t>(v.begin(), v.end()) {}
};

// TPy `BytesView` -- the read-only view over a byte buffer, and the form every
// `bytes` PARAMETER takes (`str`'s `std::string_view`). Its own type rather
// than the bare span because the standard gives `std::span` no `==`, `<=>`
// or `std::hash`, and every bytes-family comparison, lookup key and hash goes
// through the view: defined once here, a compare, a container lookup and a
// generic body instantiated at `bytes` all use the bare operators `str` does.
// The converting constructors stay IMPLICIT, unlike the owning siblings': a
// view copies nothing, so nothing is silently duplicated through it. They
// are ENUMERATED rather than inherited: the span's range constructor would
// read any contiguous byte range as this view, a bare `std::vector` --
// `list[UInt8]` -- included, in every reference qualification.
struct BytesView : std::span<const std::uint8_t> {
    using Base = std::span<const std::uint8_t>;
    BytesView() = default;
    BytesView(Base s) : Base(s) {}
    BytesView(std::span<std::uint8_t> s) : Base(s) {}
    BytesView(const Bytes& b) : Base(b) {}
    BytesView(const ByteArray& b) : Base(b) {}
    BytesView(const std::uint8_t* p, std::size_t n) : Base(p, n) {}
    template<std::contiguous_iterator It>
    BytesView(It first, It last) : Base(first, last) {}
    // The EXPLICIT escape hatch for any other contiguous byte range (a
    // hand-written helper's scratch vector): a spelled construction, never
    // a conversion, like the owners' buffer constructors.
    template<std::ranges::contiguous_range R>
        requires std::same_as<std::ranges::range_value_t<R>, std::uint8_t>
    explicit BytesView(R&& r) : Base(r) {}

    // The size test comes first: an empty span may hold (nullptr, 0), and
    // memcmp on a null pointer is undefined even at length 0.
    friend bool operator==(BytesView a, BytesView b) {
        return a.size() == b.size()
               && (a.empty()
                   || std::memcmp(a.data(), b.data(), a.size()) == 0);
    }
    friend std::strong_ordering operator<=>(BytesView a, BytesView b) {
        return std::lexicographical_compare_three_way(
            a.begin(), a.end(), b.begin(), b.end());
    }
};

// The invariant the split rests on: no TPy buffer type converts into another
// behind the programmer's back, in either direction, and neither converts from
// a bare `list[UInt8]`. A conversion between them is a TPy-level coercion the
// front end decides -- and at an OWNING sink it decides both directions are a
// sema error asking for the explicit `bytes(...)` / `bytearray(...)`; the
// surviving `bytearray_to_bytes` coercion is identity at a BORROWING `bytes`
// parameter, which is a view. Never something C++ does on its own. Asserted here rather than in a test
// because this header is compiled by every generated translation unit -- the
// same reason lookup_key.hpp pins its key spellings inline.
static_assert(!std::is_convertible_v<ByteArray, Bytes>);
static_assert(!std::is_convertible_v<Bytes, ByteArray>);
static_assert(!std::is_convertible_v<std::vector<std::uint8_t>, Bytes>);
static_assert(!std::is_convertible_v<std::vector<std::uint8_t>, ByteArray>);
static_assert(!std::is_convertible_v<std::span<const std::uint8_t>, Bytes>);
// ... while the base and the view a hand-written signature spells still bind,
// which is what deriving is for.
static_assert(std::is_convertible_v<Bytes, const std::vector<std::uint8_t>&>);
static_assert(std::is_convertible_v<ByteArray, std::span<const std::uint8_t>>);
static_assert(std::is_convertible_v<String, std::string_view>);
// ... and every owner and the bare span read as the view, never the reverse.
static_assert(std::is_convertible_v<Bytes, BytesView>);
static_assert(std::is_convertible_v<ByteArray, BytesView>);
static_assert(std::is_convertible_v<std::span<const std::uint8_t>, BytesView>);
static_assert(std::is_convertible_v<std::span<std::uint8_t>, BytesView>);
static_assert(!std::is_convertible_v<BytesView, Bytes>);
static_assert(!std::is_convertible_v<BytesView, ByteArray>);
// A bare vector reads as the view only through an explicit `BytesView(v)`,
// like the owners' explicit buffer constructors: never by conversion, in
// any reference qualification.
static_assert(!std::is_convertible_v<std::vector<std::uint8_t>, BytesView>);
static_assert(!std::is_convertible_v<std::vector<std::uint8_t>&, BytesView>);
static_assert(!std::is_convertible_v<const std::vector<std::uint8_t>&, BytesView>);
static_assert(std::is_constructible_v<BytesView, std::vector<std::uint8_t>&>);
static_assert(std::is_constructible_v<BytesView, const std::vector<std::uint8_t>&>);

// `String`, `bytes` and `BytesView` are value types; `bytearray` is not (the
// primary template's default). `bytes`'s parameter form is its view, like
// `str`'s. The view borrows another container's storage, so like the bare
// span it is not Send; the `std::span` partial specializations do not match
// a derived type, hence the rows.
template<> struct is_value_type<String> : std::true_type {};
template<> struct is_value_type<Bytes> : std::true_type {};
template<> struct is_value_type<BytesView> : std::true_type {};
template<> struct is_send<BytesView> : std::false_type {};
namespace detail {
template<> struct param_val_or_ref_impl<Bytes> {
    using type = BytesView;
};

// One hash for the whole family, so a view probes a table of owners and a
// record field holding either agrees with the table: the string_view hash
// over the bytes, and 0 for empty (CPython's `hash(b"")`), which also keeps
// an empty span's possibly-null data pointer out of the hash.
inline std::size_t hash_bytes(BytesView v) noexcept {
    return v.empty()
        ? 0u
        : std::hash<std::string_view>{}(std::string_view(
              reinterpret_cast<const char*>(v.data()), v.size()));
}
}  // namespace detail

// A `bytearray` owns a plain byte buffer with no shared refs, so it is Send;
// it is mutable, so it is not Sync. The reference-type default is neither, and
// the `std::vector` partial specialization does not match a derived type.
template<> struct is_send<ByteArray> : std::true_type {};

}  // namespace tpy

// The buffer types are program-defined, so unlike the bare span they may
// carry a `std::hash`; `noexcept` is what lets a hash table skip caching the
// hash per node (see lookup_key.hpp).
template<>
struct std::hash<::tpy::BytesView> {
    std::size_t operator()(::tpy::BytesView v) const noexcept {
        return ::tpy::detail::hash_bytes(v);
    }
};
template<>
struct std::hash<::tpy::Bytes> {
    std::size_t operator()(const ::tpy::Bytes& b) const noexcept {
        return ::tpy::detail::hash_bytes(b);
    }
};
template<>
struct std::hash<::tpy::ByteArray> {
    std::size_t operator()(const ::tpy::ByteArray& b) const noexcept {
        return ::tpy::detail::hash_bytes(b);
    }
};

// An f-string operand goes through std::format, which has no specialization
// for a program-defined type; without this a `String` operand is a hard error.
template<>
struct std::formatter<::tpy::String, char>
    : std::formatter<std::string_view, char> {
    template<class Ctx>
    auto format(const ::tpy::String& s, Ctx& ctx) const {
        return std::formatter<std::string_view, char>::format(
            std::string_view(s), ctx);
    }
};

// TPy's own containers hash through `tpy::key_hash`, but a hand-written
// std::unordered_map keyed on a String needs this. [basic.string.hash]
// guarantees the string and its view hash alike.
template<>
struct std::hash<::tpy::String> {
    std::size_t operator()(const ::tpy::String& s) const noexcept {
        return std::hash<std::string_view>{}(std::string_view(s));
    }
};
