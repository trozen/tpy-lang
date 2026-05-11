/**
 * TurboPython Runtime - Enum Utilities
 *
 * Primary template for EnumUtil<T>. Each TurboPython enum generates
 * an explicit specialization with name(), members, from_value(), and
 * try_parse() in the module's generated code.
 */

#pragma once

#include <ostream>
#include <string>
#include <type_traits>

namespace tpy {

template<typename T>
struct EnumUtil;

namespace detail {

// Single home for the "TypeName.MEMBER" enum formatting used by
// __repr__/__str__ (in dunder.hpp) and print_optional_val (in format.hpp).
// `requires` is matched by the same constraint shape used at each call
// site, so partial-template ambiguities don't arise.
template<typename T>
    requires std::is_enum_v<T> && requires(T x) { ::tpy::EnumUtil<T>::name(x); }
inline std::ostream& write_enum_repr(std::ostream& os, T x) {
    return os << ::tpy::EnumUtil<T>::type_name << "." << ::tpy::EnumUtil<T>::name(x);
}

template<typename T>
    requires std::is_enum_v<T> && requires(T x) { ::tpy::EnumUtil<T>::name(x); }
inline std::string enum_repr_string(T x) {
    return std::string(::tpy::EnumUtil<T>::type_name) + "." +
           std::string(::tpy::EnumUtil<T>::name(x));
}

} // namespace detail
} // namespace tpy
