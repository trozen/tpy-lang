/**
 * TurboPython Runtime - Protocols
 *
 * C++ concepts for structural typing, mapping Python protocols to
 * compile-time constraints.
 */

#pragma once

#include <cstdint>
#include <iterator>
#include <ranges>
#include <type_traits>

#include "dunder.hpp"
#include "ranges.hpp"

namespace tpy {

/**
 * NativeIterable concept - types that support C++ range-based for loops
 *
 * A type is NativeIterable<ElemT> if it supports begin()/end() iteration
 * and dereferencing yields ElemT. This is the "native" C++ iteration pattern.
 * Iterator<T> and Iterable<T> above use Python's __iter__/__next__ protocol.
 */
template<typename T, typename ElemT>
concept NativeIterable = requires(const T& t) {
    { std::ranges::begin(t) } -> std::input_or_output_iterator;
    { std::ranges::end(t) } -> std::sentinel_for<decltype(std::ranges::begin(t))>;
    { *std::ranges::begin(t) } -> std::convertible_to<ElemT>;
};

/**
 * ReadOnlySpanLike concept - types that expose contiguous storage as a readonly span
 *
 * A type is ReadOnlySpanLike<ElemT> if tpy::as_span(t) yields something convertible
 * to std::span<const ElemT>. This covers both builtin types (vector, array,
 * ReadOnlySpan) via as_span overloads and user types via __span__() const.
 */
template<typename T, typename ElemT>
concept ReadOnlySpanLike = requires(const T& t) {
    { tpy::as_span(t) } -> std::convertible_to<std::span<const ElemT>>;
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
 * Deref concept -- types that can be dereferenced via tpy::deref_check().
 *
 * Covers both raw pointers (Ptr[T], ReadOnlyPtr[T]) and user types with __deref__().
 */
template<typename T, typename TargetT>
concept Deref = requires(T& t) {
    { tpy::deref_check(t) };
} && std::same_as<
    std::remove_cvref_t<decltype(tpy::deref_check(std::declval<T&>()))>,
    TargetT>;

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

/**
 * Equatable concept - types that support the == operator
 */
template<typename T>
concept Equatable = requires(const T& a, const T& b) {
    { a == b } -> std::convertible_to<bool>;
};

/**
 * Truthy concept - types that support tpy::__bool__()
 *
 * A type is Truthy if tpy::__bool__(x) is valid and returns bool.
 * Used for bool() conversion on user-defined types.
 */
template<typename T>
concept Truthy = requires(const T& t) {
    { tpy::__bool__(t) } -> std::convertible_to<bool>;
};

/**
 * Stringable concept - types that support tpy::__str__()
 *
 * A type is Stringable if tpy::__str__(x) is valid and returns a string-like
 * type (std::string or std::string_view).
 */
template<typename T>
concept Stringable = requires(const T& t) {
    { std::string(tpy::__str__(t)) };
};

/**
 * Representable concept - types that support tpy::__repr__()
 *
 * A type is Representable if tpy::__repr__(x) is valid and returns a
 * string-like type (std::string or std::string_view).
 */
template<typename T>
concept Representable = requires(const T& t) {
    { std::string(tpy::__repr__(t)) };
};

/**
 * Hashable concept - types that support tpy::__hash__()
 *
 * A type is Hashable if tpy::__hash__(x) is valid and returns uint64_t.
 * Used for dict key validation and the hash() builtin.
 */
template<typename T>
concept Hashable = requires(const T& t) {
    { tpy::__hash__(t) } -> std::convertible_to<uint64_t>;
};

} // namespace tpy
