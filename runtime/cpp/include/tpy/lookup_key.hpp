/**
 * TurboPython Runtime - Lookup Keys
 *
 * A container LOOKUP (`list.remove`/`index`/`count`, set and dict membership,
 * erase and the dict key reads) takes its key as a READ-ONLY argument, so it
 * arrives in the read form -- `std::string_view` or a bare `const char[N]`
 * literal for a stored `std::string`, `tpy::BytesView` for a stored
 * `tpy::Bytes` / `tpy::ByteArray` -- while the container stores the owned
 * form. These helpers compare and hash a key in the STORED type's domain, so a
 * lookup never builds an element to find one.
 *
 * `key_hash` / `key_equal` are the hash and equality of both `ordered_set`
 * and `ordered_map`, which is also why neither container needs a `std::hash`
 * specialization on a standard type ([namespace.std] forbids one: no
 * program-defined type is involved).
 */

#pragma once

#include <concepts>
#include <cstddef>
#include <cstdint>
#include <functional>
#include <string>
#include <string_view>
#include <tuple>
#include <type_traits>
#include <utility>
#include <vector>

#include "buffer_types.hpp"

namespace tpy {

// Defined in dunder.hpp, which includes THIS header, so it cannot be included
// back: a tuple hashes by combining its elements' `tpy::__hash__`. Declared
// here so a tuple-keyed table and a record field holding the same tuple agree
// on one answer instead of two.
template<typename... Ts>
std::uint64_t __hash__(const std::tuple<Ts...>& t);

namespace detail {

template<typename X>
inline constexpr bool is_std_tuple = false;
template<typename... Ts>
inline constexpr bool is_std_tuple<std::tuple<Ts...>> = true;

}  // namespace detail

// A bare string literal (`const char[N]`, the form a member call's literal
// argument arrives in) or a `const char*`. Read as NUL-terminated, which is
// also how `std::string`'s `operator==(const char*)` reads it, so hash and
// equality agree on the same characters. A literal holding an embedded NUL
// never arrives here: the compiler spells that one `std::string_view{"...",
// N}` (`cpp_string_literal_expr`), so the terminated reading is the whole
// literal.
template<typename X>
concept c_string_key =
    std::same_as<std::decay_t<X>, const char*> || std::same_as<std::decay_t<X>, char*>;

// Compare a container's STORED element against a lookup key that may be
// spelled in a different C++ type.
template<typename T, typename U>
constexpr bool key_eq(const T& stored, const U& key) {
    if constexpr (requires { { stored == key } -> std::convertible_to<bool>; }) {
        return stored == key;
    } else {
        // A key that only CONVERTS: the last resort, and the one shape that
        // still builds an element to compare against.
        return stored == T(key);
    }
}

// Hash a key (or a stored element) so that equal values hash alike whatever
// their spelling -- the invariant a heterogeneous hash lookup rests on.
// Whether hashing U cannot throw. Every str spelling the hash table probes
// with is a string_view hash (noexcept by the standard), the bytes family's
// `std::hash` is declared noexcept (buffer_types.hpp); a tuple and a user
// type answer for themselves. Spelled as a function, not a `noexcept(expr)`,
// because `std::hash` has no specialization for a tuple and the expression
// form would instantiate one.
template<typename U>
consteval bool key_hash_nothrow() {
    if constexpr (c_string_key<U>
                  || std::same_as<std::remove_cvref_t<U>, std::string>
                  || std::same_as<std::remove_cvref_t<U>, String>
                  || std::same_as<std::remove_cvref_t<U>, std::string_view>) {
        return true;
    } else if constexpr (detail::is_std_tuple<std::remove_cvref_t<U>>) {
        // A tuple's elements are user code, which can raise, so the answer is
        // whatever `tpy::__hash__` promises -- today nothing. The table then
        // caches the hash per node, which for a combine-N-elements hash is
        // where you would want it anyway.
        return noexcept(__hash__(std::declval<const U&>()));
    } else {
        return noexcept(std::hash<std::remove_cvref_t<U>>{}(std::declval<const U&>()));
    }
}

template<typename U>
std::size_t key_hash_value(const U& v) noexcept(key_hash_nothrow<U>()) {
    if constexpr (std::same_as<std::remove_cvref_t<U>, std::string>
                         || std::same_as<std::remove_cvref_t<U>, String>
                         || std::same_as<std::remove_cvref_t<U>, std::string_view>
                         || c_string_key<U>) {
        // [basic.string.hash] guarantees a string and its view hash alike, so
        // spelling all three through the view is the same value, not a choice.
        return std::hash<std::string_view>{}(std::string_view(v));
    } else if constexpr (detail::is_std_tuple<std::remove_cvref_t<U>>) {
        return static_cast<std::size_t>(__hash__(v));
    } else {
        return std::hash<std::remove_cvref_t<U>>{}(v);
    }
}

// The hash/equality pair a hash table needs to answer a key directly.
// `is_transparent` is what enables `unordered_map::find(key)`.
struct key_hash {
    using is_transparent = void;

    // `noexcept` is load-bearing: libstdc++ caches a hash per node for a
    // functor that may throw, which would cost every set of cheap keys
    // eight bytes an element.
    template<typename U>
    std::size_t operator()(const U& v) const noexcept(key_hash_nothrow<U>()) {
        return key_hash_value(v);
    }
};

struct key_equal {
    using is_transparent = void;

    template<typename A, typename B>
    bool operator()(const A& a, const B& b) const { return key_eq(a, b); }
};

// The key spellings a HASH lookup may take without building a T.
// Deliberately a closed list: a type that merely compares equal to T hashes in
// its OWN domain (a fixed int against a BigInt), where the lookup would
// silently miss. Every spelling listed is hash-compatible with its stored
// type by construction above.
template<typename T, typename U>
concept lookup_key_for =
    ((std::same_as<T, std::string> || std::same_as<T, String>)
     && (std::same_as<std::remove_cvref_t<U>, std::string_view>
         || c_string_key<U>))
    || ((std::same_as<T, Bytes> || std::same_as<T, ByteArray>)
        && std::same_as<std::remove_cvref_t<U>, BytesView>);

// A literal key resolves to the transparent path, not to the owned overload:
// `s.discard("c")` and `d["c"]` must not build a std::string to find one.
// Pinned here because no snapshot can see which overload won. A literal
// bound to a `const KeyArg&` parameter deduces `char[N]` (the const is
// absorbed by the reference), so that is the form the pin must hold for.
static_assert(lookup_key_for<std::string, char[2]>);
static_assert(lookup_key_for<std::string, const char(&)[2]>);
static_assert(lookup_key_for<std::string, const char*>);
static_assert(lookup_key_for<std::string, std::string_view>);
static_assert(!lookup_key_for<std::string, std::string>,
              "the stored form is not a key spelling -- it is the element");
static_assert(lookup_key_for<String, std::string_view>);
static_assert(lookup_key_for<Bytes, BytesView>);
static_assert(lookup_key_for<ByteArray, BytesView>);

}  // namespace tpy
