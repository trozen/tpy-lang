/**
 * TurboPython Runtime - Type Traits
 *
 * Compile-time type classification for value vs reference semantics.
 */

#pragma once

#include <cstdint>
#include <string>
#include <string_view>
#include <type_traits>

namespace tpy {

// Forward declaration for BigInt specialization
class BigInt;

// --- Type trait for value vs reference semantics ---

/**
 * is_value_type - Type trait for determining copy vs reference semantics.
 *
 * Value types (primitives) are returned by copy when accessed from containers.
 * Object types (records, nested containers) are returned by reference.
 */
template<typename T> struct is_value_type : std::false_type {};

// Primitive value types
template<> struct is_value_type<int8_t> : std::true_type {};
template<> struct is_value_type<int16_t> : std::true_type {};
template<> struct is_value_type<int32_t> : std::true_type {};
template<> struct is_value_type<int64_t> : std::true_type {};
template<> struct is_value_type<uint8_t> : std::true_type {};
template<> struct is_value_type<uint16_t> : std::true_type {};
template<> struct is_value_type<uint32_t> : std::true_type {};
template<> struct is_value_type<uint64_t> : std::true_type {};
template<> struct is_value_type<bool> : std::true_type {};
template<> struct is_value_type<char> : std::true_type {};
template<> struct is_value_type<double> : std::true_type {};
template<> struct is_value_type<std::string> : std::true_type {};
template<> struct is_value_type<std::string_view> : std::true_type {};
template<> struct is_value_type<BigInt> : std::true_type {};

// C++ concept for the ValueType marker protocol
template<typename T>
concept ValueType = is_value_type<T>::value;

/**
 * Return type helpers for generic code.
 *
 * These pick value or reference return types based on is_value_type trait:
 * - val_or_ref_t<T>: T for value types, T& for object types (mutable)
 * - val_or_cref_t<T>: T for value types, const T& for object types (const)
 */
template<typename T>
using val_or_ref_t = std::conditional_t<is_value_type<T>::value, T, T&>;

template<typename T>
using val_or_cref_t = std::conditional_t<is_value_type<T>::value, T, const T&>;

/**
 * Parameter type helper for generic code.
 *
 * Picks parameter type based on is_value_type trait:
 * - const T& for value types (immutable in Python, compiler optimizes small types)
 * - T& for object types (mutable in Python)
 */
template<typename T>
using param_val_or_ref_t = std::conditional_t<is_value_type<T>::value, const T&, T&>;

} // namespace tpy
