/**
 * TurboPython Runtime - Protocols
 *
 * Protocol free functions and C++ concepts for structural typing.
 */

#pragma once

#include <array>
#include <cstddef>
#include <cstdint>
#include <iterator>
#include <optional>
#include <ranges>
#include <span>
#include <string_view>
#include <type_traits>
#include <vector>

#include "static_list.hpp"
#include "ranges.hpp"

namespace tpy {

// =============================================
// Protocol free functions - unified interface for dunder methods
// =============================================

/**
 * tpy::__len__ - Protocol-based length accessor
 *
 * Enables len() to work uniformly across all container types:
 * - User types: calls x.__len__() method
 * - std types: overloads call .size()
 *
 * This allows functions with protocol-typed parameters to work with
 * both user-defined and standard library types.
 */

// Overload: std::vector (most specific, checked first)
template<typename T>
int32_t __len__(const std::vector<T>& x) {
    return static_cast<int32_t>(x.size());
}

// Overload: std::array
template<typename T, std::size_t N>
int32_t __len__(const std::array<T, N>& x) {
    return static_cast<int32_t>(x.size());
}

// Overload: std::span (const and non-const)
template<typename T>
int32_t __len__(std::span<const T> x) {
    return static_cast<int32_t>(x.size());
}

template<typename T>
int32_t __len__(std::span<T> x) {
    return static_cast<int32_t>(x.size());
}

// Overload: std::string_view
inline int32_t __len__(std::string_view x) {
    return static_cast<int32_t>(x.size());
}

// Overload: const char* (string literals)
inline int32_t __len__(const char* x) {
    return static_cast<int32_t>(std::string_view(x).size());
}

// Overload: StaticList
template<typename T, std::size_t N>
int32_t __len__(const StaticList<T, N>& x) {
    return x.size();  // StaticList::size() already returns int32_t
}

// Default template: user types that define __len__() method
// This is checked last due to the requires clause
template<typename T>
    requires requires(const T& t) { { t.__len__() } -> std::convertible_to<int32_t>; }
int32_t __len__(const T& x) {
    return x.__len__();
}

// =============================================
// Concepts for structural typing
// =============================================

/**
 * Sized concept - types that support tpy::__len__()
 *
 * Matches Python's typing.Protocol approach to structural subtyping.
 * A type is Sized if tpy::__len__(x) is valid and returns int32_t.
 */
template<typename T>
concept Sized = requires(const T& t) {
    { tpy::__len__(t) } -> std::convertible_to<int32_t>;
};

/**
 * Sequence concept - types that support tpy::__len__() and indexing
 *
 * Generic protocol parameterized by element type ElemT.
 * A type is Sequence<ElemT> if it has len() and operator[](int32_t) -> ElemT.
 */
template<typename T, typename ElemT>
concept Sequence = requires(const T& t, int32_t i) {
    { tpy::__len__(t) } -> std::convertible_to<int32_t>;
    { t[i] } -> std::convertible_to<ElemT>;
};

/**
 * NativeIterable concept - types that support C++ range-based for loops
 *
 * A type is NativeIterable<ElemT> if it supports begin()/end() iteration
 * and dereferencing yields ElemT. This is the "native" C++ iteration pattern.
 *
 * Future: Iterable<T> will use Python's __iter__/__next__ protocol.
 */
template<typename T, typename ElemT>
concept NativeIterable = requires(const T& t) {
    { std::ranges::begin(t) } -> std::input_or_output_iterator;
    { std::ranges::end(t) } -> std::sentinel_for<decltype(std::ranges::begin(t))>;
    { *std::ranges::begin(t) } -> std::convertible_to<ElemT>;
};

/**
 * OptIterator concept - types that produce values lazily via __next_opt__()
 *
 * A type is OptIterator<ElemT> if calling __next_opt__() returns
 * std::optional<ElemT>. Used for lazy producers like Range and user-defined
 * iterators (whose __next__ is compiled to __next_opt__).
 */
template<typename T, typename ElemT>
concept OptIterator = requires(T& t) {
    { t.__next_opt__() } -> std::same_as<std::optional<ElemT>>;
};

/**
 * NativeContiguous concept - types with elements laid out contiguously in memory
 *
 * A type is NativeContiguous<ElemT> if it's a contiguous_range with elements
 * convertible to ElemT. Types conforming to NativeContiguous can be implicitly
 * converted to std::span.
 */
template<typename T, typename ElemT>
concept NativeContiguous = std::ranges::contiguous_range<T> &&
    std::convertible_to<std::ranges::range_reference_t<T>, ElemT>;

/**
 * MutableSequence concept - types that support len(), read indexing, and write indexing
 *
 * A type is MutableSequence<ElemT> if it satisfies Sequence<ElemT> and additionally
 * supports assignment via subscript operator (t[i] = v).
 */
template<typename T, typename ElemT>
concept MutableSequence = Sequence<T, ElemT> && requires(T& t, int32_t i, ElemT v) {
    { t[i] = v };
};

/**
 * NativeRangeConstructible concept - types that can be constructed from a range
 *
 * A type is NativeRangeConstructible<ElemT> if from_range<T> can construct it.
 * This requires an iterator-pair constructor (begin, end).
 */
template<typename T, typename ElemT>
concept NativeRangeConstructible = requires(repeat_range<ElemT> r) {
    T(std::ranges::begin(r), std::ranges::end(r));
};

/**
 * Deref concept — types that can be dereferenced via operator* or __deref__().
 *
 * Ptr[T] and ConstPtr[T] use tpy::deref_ptr() which dereferences raw pointers.
 * User types implementing Deref[T] provide __deref__() -> T& directly.
 */
template<typename T, typename TargetT>
concept Deref = requires(T& t) {
    { t.__deref__() } -> std::convertible_to<TargetT&>;
};

/**
 * Comparable concept - types that support the < operator
 *
 * A type is Comparable if it supports t < t comparison returning bool.
 * This is used for bounded type parameters like T: Comparable.
 */
template<typename T>
concept Comparable = requires(const T& a, const T& b) {
    { a < b } -> std::convertible_to<bool>;
};

} // namespace tpy
