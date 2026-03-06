/**
 * TurboPython Runtime - Iterable Operations
 *
 * Non-range overloads for container operations (list_extend, str_join, etc.)
 * that accept Iterable types (types with tpy::__iter__() but no begin()/end()).
 *
 * Included after iter_adapt.hpp so iter_adapt_container is available.
 * The primary (range-based) overloads live in container_ops.hpp and format.hpp.
 */

#pragma once

#include <ranges>
#include <string>
#include <string_view>
#include <vector>

#include "iter_adapt.hpp"
#include "ordered_map.hpp"

namespace tpy {

// All overloads take non-const Container&: iter_adapt_container requires
// mutable access since __iter__() mutates the iterator state.

// -- list_extend for non-range iterables ------------------------------------

template<typename T, typename Container>
    requires (!std::ranges::input_range<const Container>)
void list_extend(std::vector<T>& v, Container& other) {
    for (auto __range = iter_adapt_container(other); auto&& elem : __range) {
        v.push_back(std::move(elem));
    }
}

// -- str_join for non-range iterables ---------------------------------------

template<typename Container>
    requires (!std::ranges::input_range<const Container>)
inline std::string str_join(std::string_view sep, Container& items) {
    std::string result;
    bool first = true;
    for (auto __range = iter_adapt_container(items); auto&& item : __range) {
        if (!first) result.append(sep);
        result.append(std::string_view(item));
        first = false;
    }
    return result;
}

// -- from_range for non-range iterables -------------------------------------

template<typename Container, typename Iterable>
    requires (!std::ranges::input_range<Iterable>)
Container from_range(Iterable& iterable) {
    Container result;
    for (auto __range = iter_adapt_container(iterable); auto&& elem : __range) {
        result.push_back(std::move(elem));
    }
    return result;
}

// -- dict_from_pairs for non-range iterables --------------------------------

template<typename K, typename V, typename Iterable>
    requires (!std::ranges::input_range<Iterable>)
ordered_map<K, V> dict_from_pairs(Iterable& iterable) {
    ordered_map<K, V> result;
    for (auto __range = iter_adapt_container(iterable); auto&& elem : __range) {
        result.insert_or_assign(std::get<0>(elem), std::get<1>(elem));
    }
    return result;
}

} // namespace tpy
