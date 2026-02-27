/**
 * TurboPython Runtime - Dunder Free Functions
 *
 * Protocol free functions (__len__, __getitem__, __setitem__) that bridge
 * Python dunder methods to C++ types. Overloads for std types delegate to
 * .size() / tpy::get_item / tpy::set_item; the default templates forward
 * to the user type's own dunder method.
 */

#pragma once

#include <array>
#include <cstddef>
#include <cstdint>
#include <span>
#include <string>
#include <string_view>
#include <type_traits>
#include <vector>

#include "static_list.hpp"
#include "container_ops.hpp"

namespace tpy {

// =============================================
// tpy::__len__
// =============================================

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

// Overload: std::string
inline int32_t __len__(const std::string& x) {
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
// tpy::__getitem__
// =============================================

// Overload: std::vector
template<typename T>
decltype(auto) __getitem__(const std::vector<T>& x, int32_t i) {
    return get_item(x, i);
}

// Overload: StaticList
template<typename T, std::size_t N>
decltype(auto) __getitem__(const StaticList<T, N>& x, int32_t i) {
    return get_item(x, i);
}

// Overload: std::array
template<typename T, std::size_t N>
decltype(auto) __getitem__(const std::array<T, N>& x, int32_t i) {
    auto idx = normalize_index(x, i, "array index out of bounds");
    return x[idx];
}

// Overload: std::span
template<typename T>
decltype(auto) __getitem__(std::span<const T> x, int32_t i) {
    auto idx = normalize_index(x, i, "span index out of bounds");
    return x[idx];
}

// Overload: std::string
inline char __getitem__(const std::string& x, int32_t i) {
    auto idx = normalize_index(x, i, "string index out of bounds");
    return x[idx];
}

// Overload: std::string_view
inline char __getitem__(std::string_view x, int32_t i) {
    auto idx = normalize_index(x, i, "string index out of bounds");
    return x[idx];
}

// Default template: user types that define __getitem__() method
template<typename T>
    requires requires(const T& t, int32_t i) { t.__getitem__(i); }
decltype(auto) __getitem__(const T& x, int32_t i) {
    return x.__getitem__(i);
}

template<typename T>
    requires requires(T& t, int32_t i) { t.__getitem__(i); }
decltype(auto) __getitem__(T& x, int32_t i) {
    return x.__getitem__(i);
}

// =============================================
// tpy::__setitem__
// =============================================

// Overload: std::vector
template<typename T, typename V>
void __setitem__(std::vector<T>& x, int32_t i, V&& v) {
    set_item(x, i, std::forward<V>(v));
}

// Overload: StaticList
template<typename T, std::size_t N, typename V>
void __setitem__(StaticList<T, N>& x, int32_t i, V&& v) {
    set_item(x, i, std::forward<V>(v));
}

// Default template: user types that define __setitem__() method
template<typename T, typename V>
    requires requires(T& t, int32_t i, V&& val) { t.__setitem__(i, std::forward<V>(val)); }
void __setitem__(T& x, int32_t i, V&& v) {
    x.__setitem__(i, std::forward<V>(v));
}

// =============================================
// tpy::__bool__
// =============================================

// Default template: user types that define __bool__() method
template<typename T>
    requires requires(const T& t) { { t.__bool__() } -> std::convertible_to<bool>; }
bool __bool__(const T& x) {
    return x.__bool__();
}

// =============================================
// tpy::__str__
// =============================================

// Default template: user types that define __str__() method
template<typename T>
    requires requires(const T& t) { { t.__str__() } -> std::convertible_to<std::string>; }
std::string __str__(const T& x) {
    return x.__str__();
}

// =============================================
// tpy::__repr__
// =============================================

// Default template: user types that define __repr__() method
template<typename T>
    requires requires(const T& t) { { t.__repr__() } -> std::convertible_to<std::string>; }
std::string __repr__(const T& x) {
    return x.__repr__();
}

} // namespace tpy
