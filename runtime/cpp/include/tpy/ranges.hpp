/**
 * TurboPython Runtime - Range Utilities
 *
 * Utilities for list repetition and range-based construction.
 */

#pragma once

#include <cstddef>
#include <cstdint>
#include <initializer_list>
#include <iterator>
#include <ranges>
#include <vector>

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
 * from_range<Container> - Construct a container from a range.
 *
 * Constructs container using iterator-pair constructor (begin, end).
 * If container supports reserve() and range has known size, reserves first.
 * Works with std::vector, StaticList, and any container with this constructor.
 *
 * Usage: tpy::from_range<StaticList<int, 10>>(some_range)
 *        tpy::from_range<std::vector<int>>(some_range)
 */
template<typename Container, std::ranges::input_range R>
Container from_range(R&& range) {
    if constexpr (requires(Container& c) { c.reserve(std::size_t{}); } &&
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
 * collect<Container> - Collect elements from an OptIterator into a container.
 *
 * Calls __next_opt__() repeatedly until std::nullopt, pushing elements
 * into the container. Used for list(iterator) where iterator is a
 * user-defined OptIterator (not NativeIterable).
 */
template<typename Container, typename Iter>
Container collect(Iter&& iter) {
    Container result;
    while (auto opt = iter.__next_opt__()) {
        result.push_back(std::move(*opt));
    }
    return result;
}

} // namespace tpy
