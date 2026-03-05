/**
 * TurboPython Runtime - Iterator Adapter
 *
 * Wraps types with __next_opt__() into C++ input iterators (begin/end),
 * allowing unified for-loop codegen via the explicit iterator pattern.
 *
 * Two entry points:
 *   iter_adapt(iter)       -- wrap an iterator (has __next_opt__())
 *   iter_adapt_container(c) -- wrap a container (has __iter__() -> iterator)
 *
 * Single-use ranges: call begin() exactly once per instance.
 *
 * Depends on: <optional>, <iterator>, <cstddef>
 */

#pragma once

#include <cstddef>
#include <iterator>
#include <optional>
#include <ranges>

#include "dunder.hpp"

namespace tpy {

struct IterAdaptSentinel {};

template<typename Iter>
class IterAdaptIterator {
    Iter* iter_;
    using OptT = decltype(std::declval<Iter&>().__next_opt__());
    OptT cached_;
public:
    using iterator_category = std::input_iterator_tag;
    using value_type = typename OptT::value_type;
    using difference_type = std::ptrdiff_t;
    using pointer = value_type*;
    using reference = value_type&;

    explicit IterAdaptIterator(Iter& iter)
        : iter_(&iter), cached_(iter_->__next_opt__()) {}

    auto& operator*() { return *cached_; }
    const auto& operator*() const { return *cached_; }

    IterAdaptIterator& operator++() {
        cached_ = iter_->__next_opt__();
        return *this;
    }

    bool operator!=(IterAdaptSentinel) const { return cached_.has_value(); }
    bool operator==(IterAdaptSentinel) const { return !cached_.has_value(); }
};

// Adapts an iterator (type with __next_opt__()) into a C++ range.
// Holds a reference to the iterator -- caller must keep it alive.
template<typename Iter>
class IterAdaptRange {
    Iter* iter_;
public:
    explicit IterAdaptRange(Iter& iter) : iter_(&iter) {}
    IterAdaptRange(const IterAdaptRange&) = delete;
    IterAdaptRange& operator=(const IterAdaptRange&) = delete;
    IterAdaptRange(IterAdaptRange&&) = default;
    IterAdaptRange& operator=(IterAdaptRange&&) = default;
    IterAdaptIterator<Iter> begin() { return IterAdaptIterator<Iter>(*iter_); }
    IterAdaptSentinel end() { return {}; }
};

// Adapts a container (type with tpy::__iter__()) into a C++ range.
// Owns the iterator returned by tpy::__iter__().
template<typename Container>
class IterAdaptContainerRange {
    using IterT = decltype(tpy::__iter__(std::declval<Container&>()));
    IterT iter_;
public:
    explicit IterAdaptContainerRange(Container& c) : iter_(tpy::__iter__(c)) {}
    IterAdaptContainerRange(const IterAdaptContainerRange&) = delete;
    IterAdaptContainerRange& operator=(const IterAdaptContainerRange&) = delete;
    IterAdaptContainerRange(IterAdaptContainerRange&&) = default;
    IterAdaptContainerRange& operator=(IterAdaptContainerRange&&) = default;
    IterAdaptIterator<IterT> begin() { return IterAdaptIterator<IterT>(iter_); }
    IterAdaptSentinel end() { return {}; }
};

// Lightweight wrapper that forwards begin/end from a native C++ range.
// Zero overhead -- no __next_opt__/optional round-trip.
template<typename Container>
class NativeRangeRef {
    Container& c_;
public:
    explicit NativeRangeRef(Container& c) : c_(c) {}
    NativeRangeRef(const NativeRangeRef&) = delete;
    NativeRangeRef& operator=(const NativeRangeRef&) = delete;
    NativeRangeRef(NativeRangeRef&&) = default;
    NativeRangeRef& operator=(NativeRangeRef&&) = default;
    auto begin() { return c_.begin(); }
    auto end() { return c_.end(); }
};

template<typename Iter>
IterAdaptRange<Iter> iter_adapt(Iter& iter) {
    return IterAdaptRange<Iter>(iter);
}

// Native C++ ranges: bypass __iter__/optional, forward begin/end directly
template<typename Container>
    requires std::ranges::input_range<Container>
NativeRangeRef<Container> iter_adapt_container(Container& c) {
    return NativeRangeRef<Container>(c);
}

// Non-range types (user iterables): go through __iter__() + adapter
template<typename Container>
    requires (!std::ranges::input_range<Container>)
IterAdaptContainerRange<Container> iter_adapt_container(Container& c) {
    return IterAdaptContainerRange<Container>(c);
}

} // namespace tpy
