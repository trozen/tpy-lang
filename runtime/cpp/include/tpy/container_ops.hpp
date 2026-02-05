/**
 * TurboPython Runtime - Container Operations
 *
 * Helper functions for containers: index normalization, element access,
 * and list/staticlist methods (insert, remove, extend, etc.).
 */

#pragma once

#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <initializer_list>
#include <string_view>
#include <utility>
#include <vector>

#include "core.hpp"
#include "type_traits.hpp"
#include "static_list.hpp"

namespace tpy {

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
 * get_value - Get a copy of element at index (for value types).
 *
 * Use this for primitive types (Int32, BigInt, Bool, Char, str).
 * Supports negative indexing (Python semantics).
 * Panics if index is out of bounds.
 */
template <typename T>
T get_value(std::vector<T>& vec, int32_t index) {
    auto i = normalize_index(vec, index, "list index out of bounds in get_value()");
    return vec[i];
}

template <typename T>
T get_value(const std::vector<T>& vec, int32_t index) {
    auto i = normalize_index(vec, index, "list index out of bounds in get_value()");
    return vec[i];
}

/**
 * set_value - Set element at index (for value types).
 *
 * Supports negative indexing (Python semantics).
 * Panics if index is out of bounds.
 * Uses perfect forwarding to support both copy and move.
 */
template <typename T, typename V>
void set_value(std::vector<T>& vec, int32_t index, V&& value) {
    auto i = normalize_index(vec, index, "list index out of bounds in set_value()");
    vec[i] = std::forward<V>(value);
}

/**
 * get_ref - Get a reference to element at index (for object types).
 *
 * Use this for object types (records, nested containers) where
 * you need to access fields or mutate the element in-place.
 * Supports negative indexing (Python semantics).
 * Panics if index is out of bounds.
 */
template <typename T>
T& get_ref(std::vector<T>& vec, int32_t index) {
    auto i = normalize_index(vec, index, "list index out of bounds in get_ref()");
    return vec[i];
}

template <typename T>
const T& get_ref(const std::vector<T>& vec, int32_t index) {
    auto i = normalize_index(vec, index, "list index out of bounds in get_ref()");
    return vec[i];
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
 * get_item - Unified element access for std::vector.
 *
 * Returns by value for primitive types, by reference for object types.
 * Uses is_value_type trait for compile-time dispatch.
 */
template<typename T>
decltype(auto) get_item(std::vector<T>& v, int32_t index) {
    auto i = normalize_index(v, index, "list index out of bounds");
    if constexpr (is_value_type<T>::value) {
        return T(v[i]);  // Return copy for value types
    } else {
        return (v[i]);   // Return reference for object types (parens for decltype(auto))
    }
}

template<typename T>
decltype(auto) get_item(const std::vector<T>& v, int32_t index) {
    auto i = normalize_index(v, index, "list index out of bounds");
    if constexpr (is_value_type<T>::value) {
        return T(v[i]);
    } else {
        return (v[i]);
    }
}

/**
 * set_item - Unified element assignment for std::vector.
 *
 * Sets element at index. Supports negative indexing (Python semantics).
 * Panics if index is out of bounds.
 * Uses perfect forwarding to support both copy and move.
 */
template<typename T, typename V>
void set_item(std::vector<T>& v, int32_t index, V&& value) {
    auto i = normalize_index(v, index, "list index out of bounds in assignment");
    v[i] = std::forward<V>(value);
}

/**
 * get_item - Unified element access for StaticList.
 */
template<typename T, std::size_t N>
decltype(auto) get_item(StaticList<T, N>& sl, int32_t index) {
    auto i = normalize_index(sl, index, "StaticList index out of bounds");
    if constexpr (is_value_type<T>::value) {
        return T(sl[i]);
    } else {
        return (sl[i]);
    }
}

template<typename T, std::size_t N>
decltype(auto) get_item(const StaticList<T, N>& sl, int32_t index) {
    auto i = normalize_index(sl, index, "StaticList index out of bounds");
    if constexpr (is_value_type<T>::value) {
        return T(sl[i]);
    } else {
        return (sl[i]);
    }
}

/**
 * set_item - Unified element assignment for StaticList.
 */
template<typename T, std::size_t N, typename V>
void set_item(StaticList<T, N>& sl, int32_t index, V&& value) {
    auto i = normalize_index(sl, index, "StaticList index out of bounds in assignment");
    sl[i] = std::forward<V>(value);
}

/**
 * get_mut - Get mutable pointer to element (StaticList-specific, for noalloc patterns).
 */
template<typename T, std::size_t N>
T* get_mut(StaticList<T, N>& sl, int32_t index) {
    auto i = normalize_index(sl, index, "StaticList index out of bounds");
    return &sl[i];
}

/**
 * get_char - Bounds-checked character access for strings.
 *
 * Returns character at index. Supports negative indexing (Python semantics).
 * Panics if index is out of bounds.
 */
inline char get_char(std::string_view s, int32_t index) {
    auto i = normalize_index(s, index, "string index out of bounds");
    return s[i];
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
void list_extend(std::vector<T>& v, const Container& other) {
    v.insert(v.end(), other.begin(), other.end());
}

template<typename T>
void list_extend(std::vector<T>& v, std::initializer_list<T> other) {
    v.insert(v.end(), other);
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
// StaticList helper functions
// =============================================

/**
 * staticlist_extend - Python list.extend() for StaticList.
 *
 * Extends StaticList with elements from another container.
 * Panics if capacity would be exceeded.
 */
template<typename T, std::size_t N, typename Container>
void staticlist_extend(StaticList<T, N>& sl, const Container& other) {
    for (const auto& elem : other) {
        sl.push_back(elem);
    }
}

template<typename T, std::size_t N>
void staticlist_extend(StaticList<T, N>& sl, std::initializer_list<T> other) {
    for (const auto& elem : other) {
        sl.push_back(elem);
    }
}

/**
 * staticlist_insert - Python list.insert() for StaticList.
 *
 * Inserts value at index. Supports negative indexing and clamps to valid range.
 * Panics if capacity would be exceeded.
 * Does not require T to be default-constructible.
 */
template<typename T, std::size_t N, typename V>
void staticlist_insert(StaticList<T, N>& sl, int32_t index, V&& value) {
    std::ptrdiff_t i = index;
    auto sz = static_cast<std::ptrdiff_t>(sl.size());
    if (i < 0) {
        i += sz;
        if (i < 0) i = 0;
    } else if (i > sz) {
        i = sz;
    }
    // Inserting at end is just push_back
    if (i == sz) {
        sl.push_back(std::forward<V>(value));
        return;
    }
    // Store value, extend by copying last element, shift, then place value
    T temp(std::forward<V>(value));
    sl.push_back(std::move(sl[sz - 1]));
    for (std::ptrdiff_t j = sz - 1; j > i; --j) {
        sl[j] = std::move(sl[j - 1]);
    }
    sl[i] = std::move(temp);
}

/**
 * staticlist_remove - Python list.remove() for StaticList.
 *
 * Removes first occurrence of value. Panics if not found.
 */
template<typename T, std::size_t N>
void staticlist_remove(StaticList<T, N>& sl, const T& value) {
    auto it = std::find(sl.begin(), sl.end(), value);
    if (it == sl.end()) {
        tpy_panic("list.remove(x): x not in list");
    }
    // Shift elements left
    for (auto p = it; p + 1 != sl.end(); ++p) {
        *p = std::move(*(p + 1));
    }
    sl.pop_back();  // Decrease size (discards return value)
}

/**
 * staticlist_pop_at - Python list.pop(index) for StaticList.
 *
 * Removes and returns element at index. Supports negative indexing.
 * Panics if index is out of bounds.
 */
template<typename T, std::size_t N>
T staticlist_pop_at(StaticList<T, N>& sl, int32_t index) {
    auto i = normalize_index(sl, index, "pop index out of range");
    T result = std::move(sl[i]);
    // Shift elements left
    for (std::size_t j = i; j + 1 < static_cast<std::size_t>(sl.size()); ++j) {
        sl[j] = std::move(sl[j + 1]);
    }
    sl.pop_back();  // Decrease size (discards return value)
    return result;
}

/**
 * staticlist_index - Python list.index(value) for StaticList.
 *
 * Returns index of first occurrence of value. Panics if not found.
 */
template<typename T, std::size_t N>
int32_t staticlist_index(const StaticList<T, N>& sl, const T& value) {
    auto it = std::find(sl.begin(), sl.end(), value);
    if (it == sl.end()) {
        tpy_panic("list.index(x): x not in list");
    }
    return static_cast<int32_t>(it - sl.begin());
}

/**
 * staticlist_count - Python list.count(value) for StaticList.
 *
 * Returns number of occurrences of value.
 */
template<typename T, std::size_t N>
int32_t staticlist_count(const StaticList<T, N>& sl, const T& value) {
    return static_cast<int32_t>(std::count(sl.begin(), sl.end(), value));
}

/**
 * staticlist_reverse - Python list.reverse() for StaticList.
 *
 * Reverses the list in place.
 */
template<typename T, std::size_t N>
void staticlist_reverse(StaticList<T, N>& sl) {
    std::reverse(sl.begin(), sl.end());
}

} // namespace tpy
