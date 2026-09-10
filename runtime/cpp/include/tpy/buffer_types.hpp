/**
 * TurboPython Runtime - Owned string / bytes buffer types
 *
 * TPy `String`, `bytes` and `bytearray` each own a C++ type here, so a trait
 * keyed on the C++ type answers per TPy type: `std::string` then means TPy
 * `str` and nothing else, and `std::vector<uint8_t>` means `list[UInt8]` and
 * nothing else. Without that, one generic instantiation serves two TPy types
 * whose monomorphic twins take different parameter forms, and the caller has
 * to buy an owned temp to reach a slot spelled for the other type.
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

#include <cstddef>
#include <cstdint>
#include <type_traits>
#include <functional>
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

// `String` and `bytes` are value types; `bytearray` is not (the primary
// template's default). `bytes`'s parameter form is its view, like `str`'s.
template<> struct is_value_type<String> : std::true_type {};
template<> struct is_value_type<Bytes> : std::true_type {};
namespace detail {
template<> struct param_val_or_ref_impl<Bytes> {
    using type = std::span<const std::uint8_t>;
};
}  // namespace detail

// A `bytearray` owns a plain byte buffer with no shared refs, so it is Send;
// it is mutable, so it is not Sync. The reference-type default is neither, and
// the `std::vector` partial specialization does not match a derived type.
template<> struct is_send<ByteArray> : std::true_type {};

}  // namespace tpy

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
