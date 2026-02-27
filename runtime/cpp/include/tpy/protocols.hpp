/**
 * TurboPython Runtime - Protocols
 *
 * C++ concepts for structural typing, mapping Python protocols to
 * compile-time constraints.
 */

#pragma once

#include <cstdint>
#include <iterator>
#include <optional>
#include <ranges>
#include <type_traits>

#include "dunder.hpp"
#include "ranges.hpp"

namespace tpy {

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
 * Sequence concept - types that support tpy::__len__() and tpy::__getitem__()
 *
 * Generic protocol parameterized by element type ElemT.
 * A type is Sequence<ElemT> if it has len() and __getitem__(i) -> ElemT.
 */
template<typename T, typename ElemT>
concept Sequence = requires(const T& t, int32_t i) {
    { tpy::__len__(t) } -> std::convertible_to<int32_t>;
    { tpy::__getitem__(t, i) } -> std::convertible_to<ElemT>;
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
 * supports element assignment via tpy::__setitem__().
 */
template<typename T, typename ElemT>
concept MutableSequence = Sequence<T, ElemT> && requires(T& t, int32_t i, ElemT v) {
    { tpy::__setitem__(t, i, std::move(v)) };
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
 * Deref concept -- types that can be dereferenced via operator* or __deref__().
 *
 * Ptr[T] and ConstPtr[T] use tpy::deref_check() which dereferences raw pointers.
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
 * A type is Stringable if tpy::__str__(x) is valid and returns std::string.
 * Used for str() conversion on user-defined types.
 */
template<typename T>
concept Stringable = requires(const T& t) {
    { tpy::__str__(t) } -> std::convertible_to<std::string>;
};

/**
 * Representable concept - types that support tpy::__repr__()
 *
 * A type is Representable if tpy::__repr__(x) is valid and returns std::string.
 * Used for repr() conversion on user-defined types.
 */
template<typename T>
concept Representable = requires(const T& t) {
    { tpy::__repr__(t) } -> std::convertible_to<std::string>;
};

} // namespace tpy
