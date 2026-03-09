/**
 * TurboPython Runtime - Iterable Operations
 *
 * Non-range overloads for container operations (list_extend, str_join, etc.)
 * that accept Iterable types (types with tpy::__iter__() but no begin()/end()).
 *
 * Uses tpy::__iter__() + iter_adapt to iterate non-range types.
 * The primary (range-based) overloads live in container_ops.hpp and format.hpp.
 */

#pragma once

#include <ranges>
#include <string>
#include <string_view>
#include <type_traits>
#include <vector>

#include "dunder.hpp"
#include "iter_adapt.hpp"
#include "ordered_map.hpp"

namespace tpy {

// -- iter_for_loop: zero-copy dispatch for Iterable[T] for-loops -----------
//
// For native C++ ranges (list, dict, etc.), iterates directly via begin/end.
// For non-range Iterables (user types with __iter__()/__next_opt__()), falls
// back to the iter_adapt path.  This avoids the std::optional<T> wrapping
// overhead when a concrete range is passed to an Iterable[T] parameter.

template<typename Range>
struct RangeRef {
    Range& range;
    auto begin() { return range.begin(); }
    auto end() { return range.end(); }
    auto begin() const { return range.begin(); }
    auto end() const { return range.end(); }
};

template<typename Iterable>
class IterableForLoop {
    using IterT = decltype(tpy::__iter__(std::declval<Iterable&>()));
    IterT iter_;
public:
    explicit IterableForLoop(Iterable& iterable) : iter_(tpy::__iter__(iterable)) {}
    IterableForLoop(const IterableForLoop&) = delete;
    IterableForLoop& operator=(const IterableForLoop&) = delete;
    // Move-construct is safe before begin() (e.g., return from iter_for_loop).
    // Move-assign is deleted: after construction, IterAdaptIterator holds &iter_.
    IterableForLoop(IterableForLoop&&) = default;
    IterableForLoop& operator=(IterableForLoop&&) = delete;
    IterAdaptIterator<IterT> begin() { return IterAdaptIterator<IterT>(iter_); }
    IterAdaptSentinel end() { return {}; }
};

template<typename T>
    requires std::ranges::input_range<T>
RangeRef<T> iter_for_loop(T& range) {
    return RangeRef<T>{range};
}

template<typename T>
    requires (!std::ranges::input_range<T>)
IterableForLoop<T> iter_for_loop(T& iterable) {
    return IterableForLoop<T>(iterable);
}

// Forwarding-ref overloads: accept both lvalue containers (user iterables)
// and rvalue temporaries (generator expressions).  The requires clause
// excludes standard ranges so these never compete with the primary overloads
// in container_ops.hpp / format.hpp.

// -- list_extend for non-range iterables ------------------------------------

template<typename T, typename Container>
    requires (!std::ranges::input_range<std::remove_reference_t<Container>>)
void list_extend(std::vector<T>& v, Container&& other) {
    auto __iter = tpy::__iter__(other);
    for (auto __range = iter_adapt(__iter); auto&& elem : __range) {
        v.push_back(std::move(elem));
    }
}

// -- str_join for non-range iterables ---------------------------------------

template<typename Container>
    requires (!std::ranges::input_range<std::remove_reference_t<Container>>)
inline std::string str_join(std::string_view sep, Container&& items) {
    std::string result;
    bool first = true;
    auto __iter = tpy::__iter__(items);
    for (auto __range = tpy::iter_adapt(__iter); auto&& item : __range) {
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
    auto __iter = tpy::__iter__(iterable);
    for (auto __range = tpy::iter_adapt(__iter); auto&& elem : __range) {
        result.push_back(std::move(elem));
    }
    return result;
}

// -- dict_from_pairs for non-range iterables --------------------------------

template<typename K, typename V, typename Iterable>
    requires (!std::ranges::input_range<std::remove_reference_t<Iterable>>)
ordered_map<K, V> dict_from_pairs(Iterable&& iterable) {
    ordered_map<K, V> result;
    auto __iter = tpy::__iter__(iterable);
    for (auto __range = tpy::iter_adapt(__iter); auto&& elem : __range) {
        result.insert_or_assign(std::get<0>(elem), std::get<1>(elem));
    }
    return result;
}

} // namespace tpy
