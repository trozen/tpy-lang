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
#include "frame_slot.hpp"
#include "dunder.hpp"

#include <expected>
#include <optional>
#include <type_traits>

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

// Resumable-frame `for x in <Iterable>` iterator handling. The source `src` is
// already frame-resident (a captured param, a frame-held local, or a moved-in
// temporary in __for_src); these helpers manage the iterator derived from it,
// stored in the `frame_slot` the frame already declares.
//
// A self-iterator -- one whose __iter__() returns *this (S&) -- is its own
// iterator, so we keep NO iterator state (the slot stays empty) and drive the
// source's __next__() directly each step, re-read through `src` so a frame move
// can't dangle a stored self-pointer. Any other source yields an independent
// iterator (a prvalue, e.g. a container's native_iterator) that we own in the
// slot as before. A source whose __iter__ returns a reference to a *member*
// iterator is intentionally NOT treated as self (its __iter__ is not idempotent)
// and takes the owned path.
template<typename S>
inline constexpr bool is_self_iterator_v =
    std::is_lvalue_reference_v<decltype(::tpy::__iter__(std::declval<S&>()))>
    && std::is_same_v<
           std::remove_reference_t<decltype(::tpy::__iter__(std::declval<S&>()))>, S>;

template<typename Slot, typename S>
void resumable_iter_init(Slot& slot, S& src) {
    if constexpr (!is_self_iterator_v<S>) slot.emplace(::tpy::__iter__(src));
}

template<typename Slot, typename S>
decltype(auto) resumable_iter_next(Slot& slot, S& src) {
    if constexpr (is_self_iterator_v<S>) return src.__next__();
    else return (*slot).__next__();
}

} // namespace tpy
