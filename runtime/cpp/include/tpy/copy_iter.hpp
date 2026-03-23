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
