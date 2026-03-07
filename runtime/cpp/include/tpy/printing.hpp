/**
 * TurboPython Runtime - Collection Printing
 *
 * Python-style printing for containers: [a, b, c]
 */

#pragma once

#include <array>
#include <cstddef>
#include <cstdint>
#include <iostream>
#include <ranges>
#include <span>
#include <sstream>
#include <string>
#include <string_view>
#include <tuple>
#include <type_traits>
#include <vector>

#include "bigint.hpp"
#include "format.hpp"

namespace tpy {

// Forward declaration for nested container printing (defined in ordered_set.hpp)
template<typename T> class ordered_set;

// --- Collection printing (Python-style: [a, b, c]) ---

template <typename T>
struct ListPrinter {
    const T& value;
    explicit ListPrinter(const T& v) : value(v) {}
};

namespace detail {

// Forward declare for recursive nested container support
template <typename Iter>
void print_list_contents(std::ostream& os, Iter begin, Iter end);

// Forward declare tuple overload so nested containers (e.g. list[tuple]) resolve correctly
template <typename... Ts>
void print_element(std::ostream& os, const std::tuple<Ts...>& t);

// Forward declare ordered_set overload (defined in set_ops.hpp)
template <typename T>
void print_element(std::ostream& os, const ordered_set<T>& elem);

template <typename T>
void print_element(std::ostream& os, const T& elem) {
    os << elem;
}

inline void print_element(std::ostream& os, bool elem) {
    os << (elem ? "True" : "False");
}

inline void print_element(std::ostream& os, double elem) {
    os << format_float(elem);
}

inline void print_element(std::ostream& os, const std::string& elem) {
    os << '\'' << elem << '\'';
}

inline void print_element(std::ostream& os, std::string_view elem) {
    os << '\'' << elem << '\'';
}

inline void print_element(std::ostream& os, const BigInt& elem) {
    os << elem.to_string();
}

// std::vector<bool> uses proxy refs so the bool overload won't match via iterators
inline void print_element(std::ostream& os, const std::vector<bool>& elem) {
    os << '[';
    for (std::size_t i = 0; i < elem.size(); ++i) {
        if (i > 0) os << ", ";
        print_element(os, static_cast<bool>(elem[i]));
    }
    os << ']';
}

// Overloads for nested containers
template <typename T>
void print_element(std::ostream& os, const std::vector<T>& elem) {
    print_list_contents(os, elem.begin(), elem.end());
}

template <typename T, std::size_t N>
void print_element(std::ostream& os, const std::array<T, N>& elem) {
    print_list_contents(os, elem.begin(), elem.end());
}

template <typename Iter>
void print_list_contents(std::ostream& os, Iter begin, Iter end) {
    os << '[';
    bool first = true;
    for (auto it = begin; it != end; ++it) {
        if (!first) os << ", ";
        first = false;
        print_element(os, *it);
    }
    os << ']';
}

} // namespace detail

template <typename T>
std::ostream& operator<<(std::ostream& os, const ListPrinter<std::vector<T>>& p) {
    detail::print_list_contents(os, p.value.begin(), p.value.end());
    return os;
}

// std::vector<bool> uses proxy refs, so the generic iterator path won't pick up
// the bool overload of print_element. Handle it explicitly.
inline std::ostream& operator<<(std::ostream& os, const ListPrinter<std::vector<bool>>& p) {
    os << '[';
    for (std::size_t i = 0; i < p.value.size(); ++i) {
        if (i > 0) os << ", ";
        detail::print_element(os, static_cast<bool>(p.value[i]));
    }
    os << ']';
    return os;
}

template <typename T, std::size_t N>
std::ostream& operator<<(std::ostream& os, const ListPrinter<std::array<T, N>>& p) {
    detail::print_list_contents(os, p.value.begin(), p.value.end());
    return os;
}

template <typename T>
std::ostream& operator<<(std::ostream& os, const ListPrinter<std::span<T>>& p) {
    detail::print_list_contents(os, p.value.begin(), p.value.end());
    return os;
}

// Generic fallback for any iterable container (e.g. repeat_range)
template <typename C>
    requires std::ranges::input_range<C>
             && (!requires { typename std::tuple_size<C>::type; })  // exclude array
std::ostream& operator<<(std::ostream& os, const ListPrinter<C>& p) {
    detail::print_list_contents(os, std::ranges::begin(p.value), std::ranges::end(p.value));
    return os;
}

// --- Generic value printing for type parameters ---

/**
 * ValuePrinter<T> - Prints values handling both scalars and containers.
 *
 * Used for generic type parameters where T might be a value type (int32_t)
 * or a container type (std::vector). Uses ListPrinter for ranges.
 */
template<typename T>
struct ValuePrinter {
    const T& value;
    explicit ValuePrinter(const T& v) : value(v) {}
};

template<typename T>
std::ostream& operator<<(std::ostream& os, const ValuePrinter<T>& p) {
    // String types are ranges but should print as strings, not char lists
    if constexpr (std::is_same_v<T, std::string_view> ||
                  std::is_same_v<T, std::string> ||
                  std::is_same_v<T, const char*>) {
        return os << p.value;
    } else if constexpr (std::is_same_v<T, bool>) {
        return os << (p.value ? "True" : "False");
    } else if constexpr (std::is_same_v<T, double>) {
        return os << format_float(p.value);
    } else if constexpr (std::ranges::range<T>) {
        return os << ListPrinter(p.value);
    } else {
        return os << p.value;
    }
}

// --- Tuple printing (Python-style: (a, b) or (a,) for single-element) ---

namespace detail {

template <typename Tuple, std::size_t... Is>
void print_tuple_elements(std::ostream& os, const Tuple& t, std::index_sequence<Is...>) {
    ((Is == 0 ? (void)(os) : (void)(os << ", "),
      print_element(os, std::get<Is>(t))), ...);
}

template <typename... Ts>
void print_element(std::ostream& os, const std::tuple<Ts...>& t) {
    os << '(';
    print_tuple_elements(os, t, std::index_sequence_for<Ts...>{});
    if constexpr (sizeof...(Ts) == 1) {
        os << ',';
    }
    os << ')';
}

} // namespace detail

template <typename... Ts>
struct TuplePrinter {
    const std::tuple<Ts...>& value;
    explicit TuplePrinter(const std::tuple<Ts...>& v) : value(v) {}
};

// Deduction guide
template <typename... Ts>
TuplePrinter(const std::tuple<Ts...>&) -> TuplePrinter<Ts...>;

template <typename... Ts>
std::ostream& operator<<(std::ostream& os, const TuplePrinter<Ts...>& p) {
    os << '(';
    detail::print_tuple_elements(os, p.value, std::index_sequence_for<Ts...>{});
    if constexpr (sizeof...(Ts) == 1) {
        os << ',';
    }
    os << ')';
    return os;
}

// --- to_str helpers for str()/repr()/f-string on containers ---

template <typename T>
std::string list_to_str(const T& c) {
    std::ostringstream oss;
    oss << ListPrinter(c);
    return oss.str();
}

template <typename... Ts>
std::string tuple_to_str(const std::tuple<Ts...>& t) {
    std::ostringstream oss;
    oss << TuplePrinter(t);
    return oss.str();
}

// --- __str__ / __repr__ overloads for std::tuple ---

template <typename... Ts>
std::string __str__(const std::tuple<Ts...>& t) {
    return tuple_to_str(t);
}

template <typename... Ts>
std::string __repr__(const std::tuple<Ts...>& t) {
    return tuple_to_str(t);
}

} // namespace tpy
