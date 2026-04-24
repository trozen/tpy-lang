/**
 * TurboPython Runtime - Range Utilities
 *
 * Utilities for list repetition and range-based construction.
 */

#pragma once

#include <array>
#include <cstddef>
#include <cstdint>
#include <initializer_list>
#include <iterator>
#include <ranges>
#include <type_traits>
#include <vector>

#include "next_iter.hpp"

namespace tpy {

/**
 * repeat_range<T> - A range that yields elements from a sequence N times.
 *
 * Used to implement Python's list repetition: [a, b] * 3 -> [a, b, a, b, a, b]
 * Satisfies std::ranges::input_range for use with C++23 std::from_range constructors.
 * Negative counts are treated as 0 (Python semantics).
 */
template<typename T>
class repeat_range {
    std::vector<T> elements_;
    std::size_t count_;

public:
    repeat_range(int32_t count, std::initializer_list<T> elements)
        : elements_(elements), count_(count > 0 ? static_cast<std::size_t>(count) : 0) {}

    class iterator {
        const repeat_range* parent_;
        std::size_t rep_;
        std::size_t idx_;

    public:
        using iterator_category = std::input_iterator_tag;
        using value_type = T;
        using difference_type = std::ptrdiff_t;
        using pointer = const T*;
        using reference = const T&;

        iterator() : parent_(nullptr), rep_(0), idx_(0) {}
        iterator(const repeat_range* p, std::size_t r, std::size_t i)
            : parent_(p), rep_(r), idx_(i) {}

        reference operator*() const { return parent_->elements_[idx_]; }

        iterator& operator++() {
            if (++idx_ >= parent_->elements_.size()) {
                idx_ = 0;
                ++rep_;
            }
            return *this;
        }

        iterator operator++(int) { auto t = *this; ++(*this); return t; }

        bool operator==(const iterator& o) const {
            return rep_ == o.rep_ && idx_ == o.idx_;
        }
        bool operator!=(const iterator& o) const { return !(*this == o); }
    };

    iterator begin() const {
        if (count_ == 0 || elements_.empty()) return end();
        return iterator(this, 0, 0);
    }
    iterator end() const { return iterator(this, count_, 0); }

    std::size_t size() const { return count_ * elements_.size(); }

    // Support tpy::__len__() for len() calls on lazy repeat ranges
    int32_t __len__() const { return static_cast<int32_t>(size()); }
};

/**
 * Convert a range to std::vector.
 * Used for list repetition when std::from_range is unavailable (GCC < 14).
 * Pre-allocates if the range has a size() method.
 */
template<typename T, std::ranges::input_range R>
std::vector<T> to_vector(R&& range) {
    std::vector<T> result;
    if constexpr (requires { range.size(); }) {
        result.reserve(range.size());
    }
    for (auto&& elem : range) {
        result.push_back(elem);
    }
    return result;
}

/**
 * Construct a vector from move-only elements (avoids std::initializer_list copy).
 * Uses a C++17 fold expression to emplace each element.
 */
template<typename T, typename... Args>
std::vector<T> make_vector(Args&&... args) {
    std::vector<T> v;
    v.reserve(sizeof...(args));
    (v.emplace_back(std::forward<Args>(args)), ...);
    return v;
}

// Trait to detect std::array specializations
template<typename T> struct is_std_array : std::false_type {};
template<typename T, std::size_t N> struct is_std_array<std::array<T, N>> : std::true_type {};

/**
 * from_range<Container> - Construct a container from a range.
 *
 * Constructs container using iterator-pair constructor (begin, end).
 * If container supports reserve() and range has known size, reserves first.
 * For std::array (aggregate, no iterator-pair ctor), fills element-by-element.
 * Works with std::vector, std::array, and any container with
 * an iterator-pair constructor.
 *
 * Usage: tpy::from_range<std::vector<int>>(some_range)
 *        tpy::from_range<std::array<int, 5>>(some_range)
 */
template<typename Container, std::ranges::input_range R>
Container from_range(R&& range) {
    if constexpr (is_std_array<Container>::value) {
        constexpr auto N = std::tuple_size<Container>::value;
        Container result{};
        std::size_t i = 0;
        for (auto&& elem : range) {
            if (i >= N) { tpy_panic("from_range: range size exceeds array capacity"); }
            result[i++] = std::move(elem);
        }
        if (i != N) { tpy_panic("from_range: range size does not match array size"); }
        return result;
    } else if constexpr (requires(Container& c) { c.reserve(std::size_t{}); } &&
                  std::ranges::sized_range<R>) {
        Container result;
        result.reserve(std::ranges::size(range));
        result.assign(std::ranges::begin(range), std::ranges::end(range));
        return result;
    } else {
        return Container(std::ranges::begin(range), std::ranges::end(range));
    }
}

/**
 * collect<Container> - Collect elements from an iterator into a container.
 *
 * Calls __next__() repeatedly until exhaustion, pushing elements
 * into the container. Used for list(iterator) where iterator is a
 * user-defined iterator (not NativeIterable).
 */
template<typename Container, typename Iter>
Container collect(Iter&& iter) {
    Container result;
    for (;;) {
        auto __r = iter.__next__();
        if (!__r.has_value()) break;
        result.push_back(unwrap_ref_move(*__r));
    }
    return result;
}

/**
 * construct<Container> - Unified container construction from any iterable.
 *
 * Dispatches at compile time: uses begin/end iteration for types that
 * satisfy std::ranges::input_range, falls back to __next__() protocol
 * for user-defined iterators.
 *
 * Usage: tpy::construct<std::vector<int>>(some_iterable)
 */
template<typename Container, typename Arg>
Container construct(Arg&& arg) {
    if constexpr (std::ranges::input_range<std::remove_cvref_t<Arg>>) {
        return from_range<Container>(std::forward<Arg>(arg));
    } else {
        return collect<Container>(std::forward<Arg>(arg));
    }
}

/**
 * extend - Append elements from an iterable to an existing container.
 * Dual-dispatch on input_range vs __next__() protocol, matching `construct`.
 * Uses the container's iterator-pair insert for ranges (memcpy for
 * trivially-copyable payloads).
 */
template<typename Container, typename Arg>
void extend(Container& c, Arg&& arg) {
    if constexpr (std::ranges::input_range<std::remove_cvref_t<Arg>>) {
        c.insert(c.end(), std::ranges::begin(arg), std::ranges::end(arg));
    } else {
        for (;;) {
            auto __r = arg.__next__();
            if (!__r.has_value()) break;
            c.push_back(unwrap_ref_move(*__r));
        }
    }
}

} // namespace tpy
