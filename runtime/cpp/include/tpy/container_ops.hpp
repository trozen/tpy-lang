/**
 * TurboPython Runtime - Container Operations
 *
 * Helper functions for containers: index normalization, element access,
 * and list methods (insert, remove, extend, etc.).
 */

#pragma once

#include <algorithm>
#include <array>
#include <cstddef>
#include <cstdint>
#include <initializer_list>
#include <ranges>
#include <span>
#include <string_view>
#include <utility>
#include <vector>

#include "core.hpp"
#include "type_traits.hpp"

namespace tpy {

/// Sentinel for omitted slice upper bound.
inline constexpr int32_t SLICE_END = INT32_MAX;

/**
 * normalize_index - Convert Python-style index to size_t.
 *
 * Supports negative indexing: -1 is last element, -2 is second-to-last, etc.
 * Panics if index is out of bounds.
 */
template <typename Container>
std::size_t normalize_index(const Container& c, int32_t index, const char* context) {
    std::ptrdiff_t i = index;
    if (i < 0) {
        i += static_cast<std::ptrdiff_t>(c.size());
    }
    if (i < 0 || static_cast<std::size_t>(i) >= c.size()) {
        tpy_panic(context);
    }
    return static_cast<std::size_t>(i);
}

/**
 * pop_back - Python list.pop() equivalent for std::vector.
 *
 * Removes and returns the last element. Panics if vector is empty.
 */
template <typename T>
T pop_back(std::vector<T>& v) {
    if (v.empty()) {
        tpy_panic("pop from empty list");
    }
    T result = std::move(v.back());
    v.pop_back();
    return result;
}

/**
 * str_slice - Python-style string slicing with clamping semantics.
 *
 * Unlike single-index access, slicing does NOT panic on out-of-bounds:
 * indices are clamped to [0, len]. Negative indices are normalized first.
 * Bounds are int32_t; codegen uses tpy::SLICE_END as sentinel for omitted
 * upper bound, so slicing is correct for strings up to ~2GB.
 */
inline std::string_view str_slice(std::string_view s, int32_t start, int32_t stop) {
    auto len = static_cast<std::ptrdiff_t>(s.size());
    std::ptrdiff_t i = start;
    std::ptrdiff_t j = stop;
    if (i < 0) i += len;
    if (j < 0) j += len;
    i = std::clamp(i, std::ptrdiff_t{0}, len);
    j = std::clamp(j, std::ptrdiff_t{0}, len);
    if (i >= j) return {};
    return s.substr(static_cast<std::size_t>(i), static_cast<std::size_t>(j - i));
}

/**
 * list_slice - Python-style container slicing: items[start:stop].
 *
 * Returns a std::span view into the container. Indices are clamped, never panics.
 * Const containers or span<const T> yield span<const T>, mutable yield span<T>.
 * Omitted bounds use tpy::SLICE_END/0 as sentinels. Correct for containers up to ~2B elements.
 */
template <typename Container>
auto list_slice(Container&& c, int32_t start, int32_t stop) {
    using ElemRef = std::remove_pointer_t<decltype(c.data())>;
    using T = std::remove_const_t<ElemRef>;
    using SpanT = std::conditional_t<std::is_const_v<ElemRef>, std::span<const T>, std::span<T>>;
    auto len = static_cast<std::ptrdiff_t>(c.size());
    std::ptrdiff_t i = start;
    std::ptrdiff_t j = stop;
    if (i < 0) i += len;
    if (j < 0) j += len;
    i = std::clamp(i, std::ptrdiff_t{0}, len);
    j = std::clamp(j, std::ptrdiff_t{0}, len);
    if (i >= j) return SpanT{};
    return SpanT{c.data() + i, static_cast<std::size_t>(j - i)};
}

// =============================================
// std::vector list methods
// =============================================

/**
 * list_insert - Python list.insert() for std::vector.
 *
 * Inserts value at index. Supports negative indexing and clamps to valid range
 * (Python semantics: -1 inserts before last element, out-of-range clamps).
 * Uses perfect forwarding to support both copy and move.
 */
template<typename T, typename V>
void list_insert(std::vector<T>& v, int32_t index, V&& value) {
    std::ptrdiff_t i = index;
    auto sz = static_cast<std::ptrdiff_t>(v.size());
    if (i < 0) {
        i += sz;
        if (i < 0) i = 0;  // Clamp to start
    } else if (i > sz) {
        i = sz;  // Clamp to end
    }
    v.insert(v.begin() + i, std::forward<V>(value));
}

/**
 * list_remove - Python list.remove() for std::vector.
 *
 * Removes first occurrence of value. Panics if not found.
 */
template<typename T>
void list_remove(std::vector<T>& v, const T& value) {
    auto it = std::find(v.begin(), v.end(), value);
    if (it == v.end()) {
        tpy_panic("list.remove(x): x not in list");
    }
    v.erase(it);
}

/**
 * list_extend - Python list.extend() for std::vector.
 *
 * Extends vector with elements from another container.
 * Overloads handle both iterator-based containers and initializer_list.
 */
template<typename T, typename Container>
    requires std::ranges::input_range<const Container>
void list_extend(std::vector<T>& v, const Container& other) {
    v.insert(v.end(), other.begin(), other.end());
}

template<typename T>
void list_extend(std::vector<T>& v, std::initializer_list<T> other) {
    v.insert(v.end(), other);
}

/**
 * list_concat - Python list.__add__ (a + b) for std::vector.
 *
 * Returns a new vector containing elements from both vectors.
 */
template<typename T>
std::vector<T> list_concat(const std::vector<T>& a, const std::vector<T>& b) {
    std::vector<T> result;
    result.reserve(a.size() + b.size());
    result.insert(result.end(), a.begin(), a.end());
    result.insert(result.end(), b.begin(), b.end());
    return result;
}

/**
 * list_pop_at - Python list.pop(index) for std::vector.
 *
 * Removes and returns element at index. Supports negative indexing.
 * Panics if index is out of bounds.
 */
template<typename T>
T list_pop_at(std::vector<T>& v, int32_t index) {
    auto i = normalize_index(v, index, "pop index out of range");
    T result = std::move(v[i]);
    v.erase(v.begin() + i);
    return result;
}

/**
 * list_index - Python list.index(value) for std::vector.
 *
 * Returns index of first occurrence of value. Panics if not found.
 */
template<typename T>
int32_t list_index(const std::vector<T>& v, const T& value) {
    auto it = std::find(v.begin(), v.end(), value);
    if (it == v.end()) {
        tpy_panic("list.index(x): x not in list");
    }
    return static_cast<int32_t>(it - v.begin());
}

/**
 * list_count - Python list.count(value) for std::vector.
 *
 * Returns number of occurrences of value.
 */
template<typename T>
int32_t list_count(const std::vector<T>& v, const T& value) {
    return static_cast<int32_t>(std::count(v.begin(), v.end(), value));
}

/**
 * list_reverse - Python list.reverse() for std::vector.
 *
 * Reverses the list in place.
 */
template<typename T>
void list_reverse(std::vector<T>& v) {
    std::reverse(v.begin(), v.end());
}

/**
 * list_copy - Python list.copy() for std::vector.
 *
 * Returns a shallow copy of the list.
 */
template<typename T>
std::vector<T> list_copy(const std::vector<T>& v) {
    return v;
}

// =============================================
// Span helpers (as_span, as_mut_span)
// =============================================

template <typename T, std::size_t N>
inline std::span<const T> as_span(const std::array<T, N>& arr) {
    return std::span<const T>(arr);
}

template <typename T>
inline std::span<const T> as_span(const std::vector<T>& vec) {
    return std::span<const T>(vec.data(), vec.size());
}

template <typename T>
inline std::span<const T> as_span(std::span<const T> span) {
    return span;
}

template <typename T>
inline std::span<const T> as_span(std::span<T> span) {
    return span;
}

// User types with __span__(): delegate to their method.
// The specific overloads above (vector, array, span) are more specialized
// and always preferred over this template.
template <typename T>
    requires requires(const T& t) { t.__span__(); }
inline auto as_span(const T& t) {
    return t.__span__();
}

// --- Mutable span helpers ---

template <typename T, std::size_t N>
inline std::span<T> as_mut_span(std::array<T, N>& arr) {
    return std::span<T>(arr);
}

// Rvalue overload: safe when span is consumed within the full-expression (ARG context)
template <typename T, std::size_t N>
inline std::span<T> as_mut_span(std::array<T, N>&& arr) {
    return std::span<T>(arr.data(), arr.size());
}

template <typename T>
inline std::span<T> as_mut_span(std::vector<T>& vec) {
    return std::span<T>(vec.data(), vec.size());
}

template <typename T>
inline std::span<T> as_mut_span(std::span<T> span) {
    return span;
}

} // namespace tpy
