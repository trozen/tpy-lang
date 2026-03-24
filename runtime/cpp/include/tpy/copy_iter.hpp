/**
 * TurboPython Runtime - CopyIter
 *
 * Iterator adapter that copies each element from a borrowing iterator.
 * Used by copy_iter() to acknowledge element-by-element copies when
 * extending containers. Each element is copied directly into the
 * destination -- no intermediate container copy.
 *
 * Implements the TurboPython Iterator protocol (__next__/__iter__).
 */

#pragma once

#include "core.hpp"
#include "dunder.hpp"

#include <expected>

namespace tpy {

template<typename T, typename Inner>
struct CopyIter {
    Inner inner;

    // Iterator protocol: copy each element from the inner iterator.
    std::expected<T, StopIteration> __next__() {
        auto r = inner.__next__();
        if (!r.has_value()) return tpy::make_unexpected(StopIteration{});
        return T(*r);
    }

    CopyIter& __iter__() { return *this; }

    // Minimal input iterator for codegen begin/end loops and from_range.
    // Not a formal std::input_iterator (no default ctor, no operator==).
    struct Sentinel {};
    struct Iterator {
        CopyIter* parent;
        std::optional<T> current;

        Iterator& operator++() {
            auto r = parent->__next__();
            current = r.has_value() ? std::optional<T>(std::move(*r)) : std::nullopt;
            return *this;
        }
        T& operator*() { return *current; }
        bool operator!=(Sentinel) const { return current.has_value(); }
    };

    Iterator begin() {
        Iterator it{this, std::nullopt};
        ++it;  // prime with first element
        return it;
    }
    Sentinel end() { return {}; }

    friend std::ostream& operator<<(std::ostream& os, const CopyIter&) {
        return os << "<copy_iter>";
    }
};

// Factory: create CopyIter from a container by calling __iter__ and wrapping.
template<typename T, typename Container>
auto copy_iter(const Container& c) {
    auto it = tpy::__iter__(c);
    return CopyIter<T, decltype(it)>{std::move(it)};
}

} // namespace tpy
