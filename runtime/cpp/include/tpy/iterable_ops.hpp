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
#include <type_traits>
#include <vector>

#include "iter_adapt.hpp"
#include "ordered_map.hpp"

namespace tpy {

// Forwarding-ref overloads: accept both lvalue containers (user iterables)
// and rvalue temporaries (generator expressions).  The requires clause
// excludes standard ranges so these never compete with the primary overloads
// in container_ops.hpp / format.hpp.

// -- list_extend for non-range iterables ------------------------------------

template<typename T, typename Container>
    requires (!std::ranges::input_range<std::remove_reference_t<Container>>)
void list_extend(std::vector<T>& v, Container&& other) {
    for (auto __range = iter_adapt_container(other); auto&& elem : __range) {
        v.push_back(std::move(elem));
    }
}

// -- str_join for non-range iterables ---------------------------------------

template<typename Container>
    requires (!std::ranges::input_range<std::remove_reference_t<Container>>)
inline std::string str_join(std::string_view sep, Container&& items) {
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
    requires (!std::ranges::input_range<std::remove_reference_t<Iterable>>)
Container from_range(Iterable&& iterable) {
    Container result;
    for (auto __range = iter_adapt_container(iterable); auto&& elem : __range) {
        result.push_back(std::move(elem));
    }
    return result;
}

// -- dict_from_pairs for non-range iterables --------------------------------

template<typename K, typename V, typename Iterable>
    requires (!std::ranges::input_range<std::remove_reference_t<Iterable>>)
ordered_map<K, V> dict_from_pairs(Iterable&& iterable) {
    ordered_map<K, V> result;
    for (auto __range = iter_adapt_container(iterable); auto&& elem : __range) {
        result.insert_or_assign(std::get<0>(elem), std::get<1>(elem));
    }
    return result;
}

} // namespace tpy
