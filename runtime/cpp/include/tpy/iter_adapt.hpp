/**
 * TurboPython Runtime - Iterator Adapter
 *
 * Wraps types with __next_opt__() into C++ input iterators (begin/end),
 * allowing unified for-loop codegen via the explicit iterator pattern.
 *
 * Entry point:
 *   iter_adapt(iter) -- wrap an iterator (has __next_opt__())
 *
 * Single-use ranges: call begin() exactly once per instance.
 *
 * Depends on: <optional>, <iterator>, <cstddef>
 */

#pragma once

#include <cstddef>
#include <expected>
#include <iterator>
#include <optional>

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

template<typename Iter>
IterAdaptRange<Iter> iter_adapt(Iter& iter) {
    return IterAdaptRange<Iter>(iter);
}

// Generator expression wrapper: stores a mutable callable returning optional<T>,
// provides __next_opt__(), __next__(), and __iter__() so it integrates with
// both iter_adapt and direct __next__() loops.
template<typename T, typename F>
class generator_wrapper {
    F fn_;
public:
    explicit generator_wrapper(F&& fn) : fn_(std::move(fn)) {}

    std::optional<T> __next_opt__() { return fn_(); }

    std::expected<T, StopIteration> __next__() {
        auto opt = fn_();
        if (opt.has_value()) return *std::move(opt);
        return std::unexpected(StopIteration{});
    }

    generator_wrapper& __iter__() { return *this; }

    friend std::ostream& operator<<(std::ostream& os, const generator_wrapper&) {
        return os << "<generator>";
    }
};

template<typename T, typename F>
generator_wrapper<T, F> make_generator(F&& fn) {
    return generator_wrapper<T, F>(std::forward<F>(fn));
}

} // namespace tpy
