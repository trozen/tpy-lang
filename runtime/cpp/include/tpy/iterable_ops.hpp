/**
 * TurboPython Runtime - Iterable Operations
 *
 * Non-range overloads for container operations (list_extend, str_join, etc.)
 * that accept Iterable types (types with tpy::__iter__() but no begin()/end()).
 *
 * Uses tpy::__iter__() + __next__() to iterate non-range types.
 * The primary (range-based) overloads live in container_ops.hpp and format.hpp.
 */

#pragma once

#include <ranges>
#include <string>
#include <string_view>
#include <type_traits>
#include <vector>

#include "dunder.hpp"
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
    auto __iter = tpy::__iter__(other);
    for (;;) {
        auto __r = __iter.__next__();
        if (!__r.has_value()) break;
        v.push_back(unwrap_ref(*__r));
    }
}

// -- str_join for non-range iterables ---------------------------------------

template<typename Container>
    requires (!std::ranges::input_range<std::remove_reference_t<Container>>)
inline std::string str_join(std::string_view sep, Container&& items) {
    std::string result;
    bool first = true;
    auto __iter = tpy::__iter__(items);
    for (;;) {
        auto __r = __iter.__next__();
        if (!__r.has_value()) break;
        if (!first) result.append(sep);
        result.append(std::string_view(unwrap_ref(*__r)));
        first = false;
    }
    return result;
}

// -- from_range for non-range iterables -------------------------------------

template<typename Container, typename Iterable>
    requires (!std::ranges::input_range<std::remove_reference_t<Iterable>>)
Container from_range(Iterable&& iterable) {
    Container result;
    auto __iter = tpy::__iter__(iterable);
    for (;;) {
        auto __r = __iter.__next__();
        if (!__r.has_value()) break;
        result.push_back(unwrap_ref(*__r));
    }
    return result;
}

// -- dict_from_pairs for non-range iterables --------------------------------

template<typename K, typename V, typename Iterable>
    requires (!std::ranges::input_range<std::remove_reference_t<Iterable>>)
ordered_map<K, V> dict_from_pairs(Iterable&& iterable) {
    ordered_map<K, V> result;
    auto __iter = tpy::__iter__(iterable);
    for (;;) {
        auto __r = __iter.__next__();
        if (!__r.has_value()) break;
        auto&& __item = unwrap_ref(*__r);
        result.insert_or_assign(std::get<0>(__item), std::get<1>(__item));
    }
    return result;
}

} // namespace tpy
