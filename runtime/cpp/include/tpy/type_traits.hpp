/**
 * TurboPython Runtime - Type Traits
 *
 * Compile-time type classification for value vs reference semantics.
 */

#pragma once

#include <array>
#include <cstdint>
#include <string>
#include <string_view>
#include <tuple>
#include <type_traits>
#include <vector>

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

// Forward declaration for SpanIter specialization
template<typename T> struct SpanIter;

// SpanIter is a lightweight view (span + index), passed by value but borrows
template<typename T> struct is_value_type<SpanIter<T>> : std::true_type {};

// Tuples are value types (immutable in Python, always copied/moved)
template<typename... Ts> struct is_value_type<std::tuple<Ts...>> : std::true_type {};

// val_or_ref<T> is a lightweight pointer/value wrapper, always passed by value
template<typename T> struct val_or_ref;
template<typename T> struct is_value_type<val_or_ref<T>> : std::true_type {};

// C++ concept for the ValueType marker protocol
template<typename T>
concept ValueType = is_value_type<T>::value;

// --- Thread safety markers ---

/**
 * is_send - Type trait for thread-safe ownership transfer.
 *
 * Send types can be safely moved to another thread. All value types are Send
 * by default. Raw pointers and non-owning views are not Send.
 * User records specialize this trait when auto-derived by the compiler.
 */
template<typename T> struct is_send : is_value_type<T> {};

// Pointers are not Send (raw pointer, no ownership guarantee)
template<typename T> struct is_send<T*> : std::false_type {};
template<typename T> struct is_send<const T*> : std::false_type {};

// string_view is not Send (borrows from another string)
template<> struct is_send<std::string_view> : std::false_type {};

// SpanIter borrows from a span -- not safe to transfer or share
template<typename T> struct is_send<SpanIter<T>> : std::false_type {};

// Containers: Send if elements are Send
template<typename T, typename A> struct is_send<std::vector<T, A>> : is_send<T> {};
template<typename T, std::size_t N> struct is_send<std::array<T, N>> : is_send<T> {};

// Forward declarations for container specializations
template<typename K, typename V> class ordered_map;
template<typename T> class ordered_set;

// ordered_map: Send if both key and value are Send
template<typename K, typename V>
struct is_send<ordered_map<K, V>>
    : std::bool_constant<is_send<K>::value && is_send<V>::value> {};

// ordered_set: Send if element is Send
template<typename T>
struct is_send<ordered_set<T>> : is_send<T> {};

template<typename T>
concept Send = is_send<T>::value;

/**
 * is_sync - Type trait for thread-safe shared access.
 *
 * Sync types can be safely referenced from multiple threads. Immutable types
 * are Sync. Mutable containers (vector, ordered_map) are not Sync.
 * User records specialize this trait when auto-derived by the compiler.
 */
template<typename T> struct is_sync : is_value_type<T> {};

// Mutable pointers are not Sync
template<typename T> struct is_sync<T*> : std::false_type {};
// Const pointers are Sync if the pointee is Sync
template<typename T> struct is_sync<const T*> : is_sync<T> {};

// Mutable containers are not Sync
template<typename T, typename A> struct is_sync<std::vector<T, A>> : std::false_type {};
template<typename K, typename V> struct is_sync<ordered_map<K, V>> : std::false_type {};
template<typename T> struct is_sync<ordered_set<T>> : std::false_type {};
template<typename T, std::size_t N> struct is_sync<std::array<T, N>> : is_sync<T> {};

// SpanIter has mutable index_ state, not safe to share
template<typename T> struct is_sync<SpanIter<T>> : std::false_type {};

template<typename T>
concept Sync = is_sync<T>::value;

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

/**
 * val_or_ref<T> - Wrapper for iterator __next__() returns.
 *
 * Stores value types (int, str, tuple, ...) by value, non-value types
 * (records, containers) by pointer. This lets __next__() return element
 * references through std::expected (which cannot hold T&).
 *
 * T may be const-qualified (e.g. from a const_iterator). val_or_ref<const T>
 * stores const T* and get() returns const T&.
 */
template<typename T>
struct val_or_ref {
    using is_val_or_ref_tag = void;
    static constexpr bool is_val = is_value_type<std::remove_const_t<T>>::value;
    using storage_t = std::conditional_t<is_val, std::remove_const_t<T>, T*>;
    storage_t data_;

    val_or_ref(const std::remove_const_t<T>& v) requires (is_val) : data_(v) {}
    val_or_ref(T& ref) requires (!is_val) : data_(&ref) {}

    decltype(auto) get() const {
        if constexpr (is_val) return data_;
        else return (*data_);
    }
};

} // namespace tpy
