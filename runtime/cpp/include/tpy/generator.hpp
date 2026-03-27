/**
 * TurboPython Runtime - Generator Wrapper
 *
 * Wraps a callable returning optional<T> into an iterator with __next__()
 * and __iter__(), used for generator expressions.
 *
 * Depends on: <optional>, <expected>
 */

#pragma once

#include "core.hpp"
#include "next_iter.hpp"

#include <expected>
#include <optional>

namespace tpy {

// Generator expression wrapper: stores a mutable callable returning optional<T>,
// provides __next__() and __iter__() so it integrates with direct __next__() loops.
// Inherits begin()/end() from next_iter_mixin so C++ range-for works too.
template<typename T, typename F>
class generator_wrapper : public next_iter_mixin<generator_wrapper<T, F>, T> {
    F fn_;
public:
    explicit generator_wrapper(F&& fn) : fn_(std::move(fn)) {}

    std::expected<T, StopIteration> __next__() {
        auto opt = fn_();
        if (opt.has_value()) return *std::move(opt);
        return tpy::make_unexpected(StopIteration{});
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
