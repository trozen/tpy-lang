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
#include <format>
#include <functional>
#include <initializer_list>
#include <ranges>
#include <span>
#include <string_view>
#include <type_traits>
#include <utility>
#include <vector>

#include "core.hpp"
#include "lookup_key.hpp"
#include "slice.hpp"
#include "type_traits.hpp"

namespace tpy {

/// Sentinel for omitted slice upper bound.
inline constexpr int32_t SLICE_END = INT32_MAX;
/// Sentinel for omitted slice bound in stepped slicing (distinct from SLICE_END
/// because stepped slicing needs to distinguish None from 0 -- negative step
/// reverses the default start/stop direction).
/// Note: collides with INT32_MIN as a valid int32 value. In practice this is
/// harmless -- no container can have 2^31 elements, so the clamped result is
/// identical whether the bound is treated as "absent" or as -2147483648.
inline constexpr int32_t SLICE_NONE = INT32_MIN;

/**
 * normalize_index - Convert Python-style index to size_t.
 *
 * Supports negative indexing: -1 is last element, -2 is second-to-last, etc.
 * Throws IndexError if index is out of bounds.
 */
template <typename Container>
std::size_t normalize_index(const Container& c, int32_t index, const char* context) {
    std::ptrdiff_t i = index;
    if (i < 0) {
        i += static_cast<std::ptrdiff_t>(c.size());
    }
    if (i < 0 || static_cast<std::size_t>(i) >= c.size()) {
        raise_index_error(context);
    }
    return static_cast<std::size_t>(i);
}

/**
 * pop_back - Python list.pop() equivalent for std::vector.
 *
 * Removes and returns the last element. Throws IndexError if vector is empty.
 */
template <typename T>
T pop_back(std::vector<T>& v) {
    if (v.empty()) {
        raise_index_error("pop from empty list");
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

/// BasicSlice overload: unpacks start/stop from the slice object.
inline std::string_view str_slice(std::string_view s, BasicSlice sl) {
    return str_slice(s, sl.start.value_or(0), sl.stop.value_or(SLICE_END));
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

/// BasicSlice overload: unpacks start/stop from the slice object.
template <typename Container>
auto list_slice(Container&& c, BasicSlice sl) {
    return list_slice(std::forward<Container>(c),
                      sl.start.value_or(0), sl.stop.value_or(SLICE_END));
}

/**
 * list_set_slice - Python-style slice assignment: items[start:stop] = values.
 * Replaces elements [start, stop) with values. Can resize the vector.
 * Indices are clamped (Python semantics).
 */
template<typename T, typename Range>
    requires std::ranges::input_range<const Range>
void list_set_slice(std::vector<T>& vec, int32_t start, int32_t stop, const Range& values) {
    auto len = static_cast<std::ptrdiff_t>(vec.size());
    std::ptrdiff_t i = start, j = stop;
    if (i < 0) i += len;
    if (j < 0) j += len;
    i = std::clamp(i, std::ptrdiff_t{0}, len);
    j = std::clamp(j, std::ptrdiff_t{0}, len);
    // Clamp reversed range to empty insertion (matches Python: a[5:2] = [...] inserts at 5)
    if (j < i) j = i;
    // `a[1:3] = a`: the erase and insert below move the elements `values`
    // reads. This guard is the only one vector<bool> gets -- it has no
    // data() for the view check below.
    if constexpr (std::is_convertible_v<const std::remove_cvref_t<Range>*,
                                        const std::vector<T>*>) {
        if (static_cast<const std::vector<T>*>(&values) == &vec) {
            std::vector<T> copy = values;
            vec.erase(vec.begin() + i, vec.begin() + j);
            vec.insert(vec.begin() + i, copy.begin(), copy.end());
            return;
        }
    }
    // A view into vec itself (`a[0:2] = a[0:5]`) is the same hazard; only a
    // contiguous range of T can be one.
    constexpr bool may_alias =
        !std::is_same_v<T, bool>
        && std::ranges::contiguous_range<const Range>
        && std::is_same_v<std::ranges::range_value_t<const Range>, T>;
    bool aliases = false;
    if constexpr (may_alias) {
        const T* p = std::ranges::data(values);
        aliases = !std::less<const T*>{}(p, vec.data())
                  && std::less<const T*>{}(p, vec.data() + vec.size());
    }
    if constexpr (std::ranges::sized_range<const Range>) {
        auto n = static_cast<std::ptrdiff_t>(std::ranges::size(values));
        if (n == j - i) {
            // Same length: overwrite in place instead of shifting the tail
            // out and back.
            if (n == 0) return;
            if constexpr (may_alias) {
                if (aliases) {
                    const T* src = std::ranges::data(values);
                    T* dst = vec.data() + i;
                    if (src == dst) return;
                    // A source starting before the target overlaps its tail;
                    // copy from the back so each element is read before it
                    // is overwritten.
                    if (src < dst) {
                        std::copy_backward(src, src + n, dst + n);
                    } else {
                        std::copy(src, src + n, dst);
                    }
                    return;
                }
            }
            std::copy(std::ranges::begin(values), std::ranges::end(values),
                      vec.begin() + i);
            return;
        }
    }
    if (aliases) {
        std::vector<T> copy(std::ranges::begin(values), std::ranges::end(values));
        vec.erase(vec.begin() + i, vec.begin() + j);
        vec.insert(vec.begin() + i, copy.begin(), copy.end());
        return;
    }
    vec.erase(vec.begin() + i, vec.begin() + j);
    vec.insert(vec.begin() + i, values.begin(), values.end());
}

/// BasicSlice overload: unpacks start/stop from the slice object.
template<typename T, typename Range>
    requires std::ranges::input_range<const Range>
void list_set_slice(std::vector<T>& vec, BasicSlice sl, const Range& values) {
    list_set_slice(vec, sl.start.value_or(0), sl.stop.value_or(SLICE_END), values);
}

// =============================================
// Stepped slice helpers (a[start:stop:step])
// =============================================

namespace detail {

/// Resolve SLICE_NONE bounds for stepped slicing. When step > 0, default
/// start is 0 and default stop is len. When step < 0, default start is
/// len-1 and default stop is -(len+1) (i.e., one before the beginning).
struct SteppedSliceBounds {
    std::ptrdiff_t start;
    std::ptrdiff_t stop;
    std::ptrdiff_t step;
};

inline SteppedSliceBounds resolve_stepped_bounds(
        int32_t raw_start, int32_t raw_stop, int32_t raw_step,
        std::ptrdiff_t len) {
    if (raw_step == 0) raise_value_error("slice step cannot be zero");
    auto step = static_cast<std::ptrdiff_t>(raw_step);
    std::ptrdiff_t start, stop;
    if (raw_start == SLICE_NONE) {
        start = (step > 0) ? 0 : len - 1;
    } else {
        start = raw_start;
        if (start < 0) start += len;
        // Positive step: clamp to [0, len]. Negative step: clamp to [-1, len-1]
        // (start=-1 means "before the beginning" -> empty result).
        start = std::clamp(start,
                           (step > 0) ? std::ptrdiff_t{0} : std::ptrdiff_t{-1},
                           (step > 0) ? len : len - 1);
    }
    if (raw_stop == SLICE_NONE) {
        stop = (step > 0) ? len : std::ptrdiff_t{-1};
    } else {
        stop = raw_stop;
        if (stop < 0) stop += len;
        stop = std::clamp(stop, std::ptrdiff_t{-1}, len);
    }
    return {start, stop, step};
}

} // namespace detail

/**
 * str_stepped_slice - Python-style stepped string slicing: s[start:stop:step].
 * Returns a new owned string (non-contiguous).
 */
inline std::string str_stepped_slice(std::string_view s, int32_t start, int32_t stop, int32_t step) {
    auto len = static_cast<std::ptrdiff_t>(s.size());
    auto [i, j, st] = detail::resolve_stepped_bounds(start, stop, step, len);
    std::string result;
    if (st > 0) {
        for (auto k = i; k < j; k += st)
            result += s[static_cast<std::size_t>(k)];
    } else {
        for (auto k = i; k > j; k += st)
            result += s[static_cast<std::size_t>(k)];
    }
    return result;
}

/**
 * list_stepped_slice - Python-style stepped container slicing: c[start:stop:step].
 * Returns a new owned vector (non-contiguous).
 */
template <typename Container>
auto list_stepped_slice(const Container& c, int32_t start, int32_t stop, int32_t step) {
    using T = std::remove_const_t<std::remove_reference_t<decltype(c[0])>>;
    auto len = static_cast<std::ptrdiff_t>(c.size());
    auto [i, j, st] = detail::resolve_stepped_bounds(start, stop, step, len);
    std::vector<T> result;
    if (st > 0) {
        for (auto k = i; k < j; k += st)
            result.push_back(c[static_cast<std::size_t>(k)]);
    } else {
        for (auto k = i; k > j; k += st)
            result.push_back(c[static_cast<std::size_t>(k)]);
    }
    return result;
}

/// Slice overloads: unpack start/stop/step from the slice object.
inline std::string str_stepped_slice(std::string_view s, Slice sl) {
    return str_stepped_slice(s, sl.start.value_or(SLICE_NONE),
                             sl.stop.value_or(SLICE_NONE),
                             sl.step.value_or(1));
}

template <typename Container>
auto list_stepped_slice(const Container& c, Slice sl) {
    return list_stepped_slice(c, sl.start.value_or(SLICE_NONE),
                              sl.stop.value_or(SLICE_NONE),
                              sl.step.value_or(1));
}

namespace detail {

/// `vec[start:stop:step] = values` for a step other than 1: `values` must
/// match the selection's size, except that an empty `values` deletes the
/// selection when `empty_deletes` (bytearray; a list raises). `what` names
/// the right-hand side in the size-mismatch message.
template<typename T, typename Range>
void set_extended_slice(std::vector<T>& vec, int32_t start, int32_t stop, int32_t step,
                        const Range& values, bool empty_deletes, const char* what) {
    auto len = static_cast<std::ptrdiff_t>(vec.size());
    auto [i, j, st] = resolve_stepped_bounds(start, stop, step, len);
    std::ptrdiff_t n = 0;
    if (st > 0 && i < j) n = (j - i - 1) / st + 1;
    else if (st < 0 && i > j) n = (i - j - 1) / (-st) + 1;
    // Collected before any write: `values` may be a view of vec.
    std::vector<T> vals(std::ranges::begin(values), std::ranges::end(values));
    if (vals.empty() && empty_deletes) {
        if (n == 0) return;
        std::ptrdiff_t lo = st > 0 ? i : i + (n - 1) * st;
        std::ptrdiff_t stride = st > 0 ? st : -st;
        std::ptrdiff_t w = lo, d = 0;
        for (std::ptrdiff_t r = lo; r < len; ++r) {
            if (d < n && r == lo + d * stride) {
                ++d;
                continue;
            }
            vec[static_cast<std::size_t>(w++)] = std::move(vec[static_cast<std::size_t>(r)]);
        }
        // erase, not resize: resize would need a default-constructible T.
        vec.erase(vec.begin() + w, vec.end());
        return;
    }
    if (static_cast<std::ptrdiff_t>(vals.size()) != n) {
        raise_value_error("attempt to assign {} of size {} to extended slice of size {}",
                          what, vals.size(), n);
    }
    for (std::size_t idx = 0; idx < vals.size(); ++idx) {
        vec[static_cast<std::size_t>(i + static_cast<std::ptrdiff_t>(idx) * st)] = std::move(vals[idx]);
    }
}

}  // namespace detail

/**
 * list_set_stepped_slice - Python-style stepped slice assignment: a[start:stop:step] = values.
 * Replaces elements at stepped positions. Unlike basic slice assignment, the RHS
 * must have exactly the same number of elements as the slice selects (Python semantics).
 * Exception: step==1 is equivalent to basic slice assignment (allows resize).
 */
template<typename T, typename Range>
    requires std::ranges::input_range<const Range>
void list_set_stepped_slice(std::vector<T>& vec, int32_t start, int32_t stop, int32_t step, const Range& values) {
    // step==1 is equivalent to basic slice assignment (allows resize)
    if (step == 1) {
        auto resolved_start = (start == SLICE_NONE) ? 0 : start;
        auto resolved_stop = (stop == SLICE_NONE) ? SLICE_END : stop;
        list_set_slice(vec, resolved_start, resolved_stop, values);
        return;
    }
    detail::set_extended_slice(vec, start, stop, step, values, false, "sequence");
}

/// Slice overload: unpacks start/stop/step from the slice object.
template<typename T, typename Range>
    requires std::ranges::input_range<const Range>
void list_set_stepped_slice(std::vector<T>& vec, Slice sl, const Range& values) {
    list_set_stepped_slice(vec, sl.start.value_or(SLICE_NONE),
                           sl.stop.value_or(SLICE_NONE),
                           sl.step.value_or(1), values);
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
 *
 * The lookup key is its own template parameter: a lookup argument is
 * read-only, so a `str`/`bytes` key arrives as a view over the element's
 * owned form and `key_eq` compares the two directly. A second parameter is
 * also what keeps `T` deducible -- deducing one `T` from the vector AND the
 * key fails for every spelling but the element type itself.
 */
template<typename T, typename U>
void list_remove(std::vector<T>& v, const U& value) {
    auto it = std::find_if(v.begin(), v.end(),
                           [&](const T& e) { return key_eq(e, value); });
    if (it == v.end()) {
        raise_value_error("list.remove(x): x not in list");
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
    requires std::ranges::input_range<std::remove_cvref_t<Container>>
void list_extend(std::vector<T>& v, Container&& other) {
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
 * Throws IndexError if index is out of bounds.
 */
template<typename T>
T list_pop_at(std::vector<T>& v, int32_t index) {
    auto i = normalize_index(v, index, "pop index out of range");
    T result = std::move(v[i]);
    v.erase(v.begin() + static_cast<std::ptrdiff_t>(i));
    return result;
}

/**
 * list_index - Python list.index(value) for std::vector.
 *
 * Returns index of first occurrence of value. Panics if not found.
 * Templated on the lookup key like list_remove.
 */
template<typename T, typename U>
int32_t list_index(const std::vector<T>& v, const U& value) {
    auto it = std::find_if(v.begin(), v.end(),
                           [&](const T& e) { return key_eq(e, value); });
    if (it == v.end()) {
        raise_value_error("list.index(x): x not in list");
    }
    return static_cast<int32_t>(it - v.begin());
}

/**
 * list_count - Python list.count(value) for std::vector.
 *
 * Returns number of occurrences of value. Templated on the lookup key like
 * list_remove.
 */
template<typename T, typename U>
int32_t list_count(const std::vector<T>& v, const U& value) {
    return static_cast<int32_t>(
        std::count_if(v.begin(), v.end(),
                      [&](const T& e) { return key_eq(e, value); }));
}

/**
 * seq_contains - Python `needle in haystack` over a sequence.
 *
 * Python's containment is `needle is e or needle == e` per element, so an
 * object that IS an element is present even when its `__eq__` is not
 * reflexive -- a record with a NaN field is the everyday witness. Plain
 * `std::ranges::contains` answers with `==` alone and reports such a needle
 * absent.
 *
 * Identity exists only where needle and element can be the same object, so
 * the identity leg is instantiated only for a needle of the range's own class
 * over lvalue elements; every other pairing has no identity to test and keeps
 * the `==`-only answer. A BORROW-form needle reaches this as an `Elem*` (a
 * record local in a resumable frame is spelled that way), which is the same
 * object seen through a pointer -- it takes the same leg. That pointer leg
 * dereferences the needle, so it requires a non-null one; codegen only spells
 * it for a borrow of live storage, never for a nullable source.
 */
template<typename Range, typename Needle>
    requires std::ranges::input_range<Range>
constexpr bool seq_contains(Range&& haystack, const Needle& needle) {
    using ElemRef = decltype(*std::ranges::begin(haystack));
    using Elem = std::remove_cvref_t<ElemRef>;
    using Pointee = std::remove_cv_t<std::remove_pointer_t<Needle>>;
    constexpr bool lvalue_elems = std::is_lvalue_reference_v<ElemRef>;
    if constexpr (lvalue_elems && std::is_class_v<Needle>
                  && std::is_same_v<Elem, Needle>) {
        for (auto&& e : haystack) {
            if (&e == &needle || e == needle) return true;
        }
        return false;
    } else if constexpr (lvalue_elems && std::is_pointer_v<Needle>
                         && std::is_class_v<Pointee>
                         && std::is_same_v<Elem, Pointee>) {
        for (auto&& e : haystack) {
            if (&e == needle || e == *needle) return true;
        }
        return false;
    } else {
        return std::ranges::contains(haystack, needle);
    }
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
 * sort_in_place - Python .sort() for any mutable random-access range
 * (std::vector, std::span). Stable ascending sort to match CPython's Timsort.
 * Forwarding-ref so a returned-by-value view (an rvalue std::span from
 * __span__(), which still aliases the owner's buffer) binds too.
 */
template<typename C>
void sort_in_place(C&& c) {
    std::stable_sort(c.begin(), c.end());
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
