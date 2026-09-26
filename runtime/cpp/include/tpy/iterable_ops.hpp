/**
 * TurboPython Runtime - Iterable Operations
 *
 * Non-range overloads for container operations (list_extend, str_join, etc.)
 * that accept Iterable types (types with tpy::__iter__() but no begin()/end()).
 *
 * They loop over `::tpy::iter_range`, the begin()/end() face of the iterator
 * `__iter__()` returns. The primary (range-based) overloads live in
 * container_ops.hpp and format.hpp; building a container from a non-range is
 * `construct` / `set_construct` / `dict_construct`.
 */

#pragma once

#include <ranges>
#include <string>
#include <string_view>
#include <type_traits>
#include <vector>

#include "container_ops.hpp"
#include "dunder.hpp"

namespace tpy {

// Forwarding-ref overloads: accept both lvalue containers (user iterables)
// and rvalue temporaries (generator expressions).  The requires clause
// excludes standard ranges so these never compete with the primary overloads
// in container_ops.hpp / format.hpp.

// -- list_extend for non-range iterables ------------------------------------

template<typename T, typename Container>
    requires (!std::ranges::input_range<std::remove_reference_t<Container>>)
void list_extend(std::vector<T>& v, Container&& other) {
    for (auto&& e : ::tpy::iter_range(other)) v.push_back(e);
}

// -- list_set_slice for non-range iterables ---------------------------------

template<typename T, typename Container>
    requires (!std::ranges::input_range<std::remove_reference_t<Container>>)
void list_set_slice(std::vector<T>& vec, BasicSlice sl, Container&& other) {
    std::vector<T> tmp;
    for (auto&& e : ::tpy::iter_range(other)) tmp.push_back(e);
    list_set_slice(vec, sl, tmp);
}

// -- list_set_stepped_slice for non-range iterables -------------------------

template<typename T, typename Container>
    requires (!std::ranges::input_range<std::remove_reference_t<Container>>)
void list_set_stepped_slice(std::vector<T>& vec, Slice sl, Container&& other) {
    std::vector<T> tmp;
    for (auto&& e : ::tpy::iter_range(other)) tmp.push_back(e);
    list_set_stepped_slice(vec, sl, tmp);
}

// -- str_join for non-range iterables ---------------------------------------

template<typename Container>
    requires (!std::ranges::input_range<std::remove_reference_t<Container>>)
inline std::string str_join(std::string_view sep, Container&& items) {
    std::string result;
    bool first = true;
    for (auto&& e : ::tpy::iter_range(items)) {
        if (!first) result.append(sep);
        result.append(std::string_view(e));
        first = false;
    }
    return result;
}

} // namespace tpy
