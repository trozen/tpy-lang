/**
 * TurboPython Runtime - next_iter adapter
 *
 * Provides begin()/end() for any type with __next__() returning
 * std::expected<T, StopIteration>. This bridges the TPy Iterator protocol
 * (__next__/__iter__) to C++ range-for loops.
 *
 * Usage: inherit from next_iter_mixin<Derived, T> to gain begin()/end().
 */

#pragma once

#include "core.hpp"

#include <expected>
#include <optional>

namespace tpy {

struct NextSentinel {};

template<typename Parent, typename T>
struct NextIterator {
    Parent* parent;
    std::optional<T> current;

    NextIterator& operator++() {
        auto r = parent->__next__();
        current = r.has_value() ? std::optional<T>(std::move(*r)) : std::nullopt;
        return *this;
    }
    T& operator*() { return *current; }
    const T& operator*() const { return *current; }
    bool operator!=(NextSentinel) const { return current.has_value(); }
};

// CRTP mixin: adds begin()/end() to any class with __next__().
template<typename Derived, typename T>
struct next_iter_mixin {
    NextIterator<Derived, T> begin() {
        NextIterator<Derived, T> it{static_cast<Derived*>(this), std::nullopt};
        ++it;  // prime with first element
        return it;
    }
    NextSentinel end() { return {}; }
};

} // namespace tpy
