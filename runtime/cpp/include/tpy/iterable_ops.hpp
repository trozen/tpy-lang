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

#include <iterator>
#include <ranges>
#include <string>
#include <string_view>
#include <type_traits>
#include <vector>

#include "bytes_ops.hpp"
#include "container_ops.hpp"
#include "dunder.hpp"

namespace tpy {

// Forwarding-ref overloads: accept both lvalue containers (user iterables)
// and rvalue temporaries (generator expressions).  The requires clause
// excludes standard ranges so these never compete with the primary overloads
// in container_ops.hpp / format.hpp. The collectors (extend, slice
// assignment) take each element through `take_elem`: one the iterator owns (a
// generator's fresh value, an object handed over by `own_iter`) is moved
// in, one it lends is copied. An object handed over (`Container` not an
// lvalue reference, a record with a consuming `__iter__`) is traversed
// through its consuming iterator (`handover_range`).

// -- list_extend for non-range iterables ------------------------------------

template<typename T, typename Container>
    requires (!::tpy::native_input_range<std::remove_reference_t<Container>>)
std::vector<T>& list_extend(std::vector<T>& v, Container&& other) {
    auto&& src = ::tpy::handover_range<Container>(other);
    auto&& r = ::tpy::iter_range(src);
    auto end = r.end();
    for (auto it = r.begin(); it != end; ++it) v.push_back(take_elem(it));
    return v;
}

// -- list_set_slice for non-range iterables ---------------------------------

namespace detail {
// A slice store's right side, collected as CPython's slice assignment does
// (`PySequence_Fast`): it takes `iter()` of the object, then lists THAT
// iterator, which asks it its own `__iter__` -- so an object handed over is
// moved into its consuming iterator, which `iter_range` then asks again.
// That second `__iter__` call is CPython's, deliberately unlike `step_range`.
template<typename T, typename Container>
std::vector<T> slice_values(Container& other) {
    std::vector<T> tmp;
    auto fill = [&](auto& src) {
        auto&& r = ::tpy::iter_range(src);
        auto end = r.end();
        for (auto it = r.begin(); it != end; ++it) tmp.push_back(take_elem(it));
    };
    if constexpr (hands_over_v<Container, std::remove_reference_t<Container>>) {
        auto consuming = ::tpy::own_iter(std::move(other));
        fill(consuming);
    } else {
        fill(other);
    }
    return tmp;
}
} // namespace detail

template<typename T, typename Container>
    requires (!::tpy::native_input_range<std::remove_reference_t<Container>>)
void list_set_slice(std::vector<T>& vec, BasicSlice sl, Container&& other) {
    std::vector<T> tmp = detail::slice_values<T, Container>(other);
    list_set_slice(vec, sl, std::ranges::subrange(std::make_move_iterator(tmp.begin()),
                                                  std::make_move_iterator(tmp.end())));
}

// -- list_set_stepped_slice for non-range iterables -------------------------

template<typename T, typename Container>
    requires (!::tpy::native_input_range<std::remove_reference_t<Container>>)
void list_set_stepped_slice(std::vector<T>& vec, Slice sl, Container&& other) {
    // CPython rejects a zero step before it runs the right-hand iterator.
    if (sl.step.value_or(1) == 0) raise_value_error("slice step cannot be zero");
    std::vector<T> tmp = detail::slice_values<T, Container>(other);
    list_set_stepped_slice(vec, sl, std::ranges::subrange(std::make_move_iterator(tmp.begin()),
                                                          std::make_move_iterator(tmp.end())));
}

// -- bytearray_set_stepped_slice for non-range iterables --------------------

template<typename Container>
    requires (!::tpy::native_input_range<std::remove_reference_t<Container>>)
void bytearray_set_stepped_slice(ByteArray& b, Slice sl, Container&& other) {
    // CPython rejects a zero step before it runs the right-hand iterator.
    if (sl.step.value_or(1) == 0) raise_value_error("slice step cannot be zero");
    std::vector<uint8_t> tmp;
    auto&& r = ::tpy::iter_range(other);
    auto end = r.end();
    for (auto it = r.begin(); it != end; ++it) tmp.push_back(take_elem(it));
    bytearray_set_stepped_slice(b, sl, tmp);
}

// -- str_join for non-range iterables ---------------------------------------

template<typename Container>
    requires (!::tpy::native_input_range<std::remove_reference_t<Container>>)
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
