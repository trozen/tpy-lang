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
#include <string>
#include <string_view>
#include <type_traits>
#include <vector>

#include "static_list.hpp"
#include "bigint.hpp"

namespace tpy {

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

template <typename T>
void print_element(std::ostream& os, const T& elem) {
    os << elem;
}

inline void print_element(std::ostream& os, const BigInt& elem) {
    os << elem.to_string();
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

template <typename T, std::size_t N>
std::ostream& operator<<(std::ostream& os, const ListPrinter<StaticList<T, N>>& p) {
    os << '[';
    for (int32_t i = 0; i < p.value.size(); ++i) {
        if (i > 0) os << ", ";
        detail::print_element(os, p.value[static_cast<std::size_t>(i)]);
    }
    os << ']';
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
    } else if constexpr (std::ranges::range<T>) {
        return os << ListPrinter(p.value);
    } else {
        return os << p.value;
    }
}

} // namespace tpy
