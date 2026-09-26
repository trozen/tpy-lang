/**
 * TurboPython Runtime - CopyIter
 *
 * Iterator adapter that copies each element its source yields.
 * Used by copy_iter() to acknowledge element-by-element copies when
 * extending containers. Each element is copied directly into the
 * destination -- no intermediate container copy.
 *
 * The source is held the way a combinator holds an argument: an lvalue is
 * borrowed, a temporary is owned (a container, a dict view, a user record, an
 * iterator alike), so a temporary lives exactly as long as the adapter.
 *
 * Implements the TurboPython Iterator protocol (__next__/__iter__).
 */

#pragma once

#include "core.hpp"
#include "dunder.hpp"
#include "itertools.hpp"

#include <expected>
#include <utility>

namespace tpy {

template<typename T, typename Source>
struct CopyIter {
    Source inner;

    // `__iter__` is user code CPython runs when the copy starts, and a
    // record's separate iterator may point into the record: both happen here,
    // in the adapter's final place.
    template<typename A>
    CopyIter(std::in_place_t, A&& src) : inner(std::forward<A>(src)) { inner.start(); }

    // Iterator protocol: copy each element from the inner iterator.
    // unwrap_ref_move copies from a val_or_ref that BORROWS and moves out of
    // an owned value -- including a val_or_ref's own copy, which is already
    // the copy this adapter exists to make; the tuple overload handles nested
    // val_or_ref elements.
    std::expected<T, StopIteration> __next__() {
        auto r = inner.__next__();
        if (!r.has_value()) return tpy::make_unexpected(StopIteration{});
        return T(unwrap_ref_move(*r));
    }

    CopyIter& __iter__() { return *this; }

    // Minimal input iterator for codegen begin/end loops (for-each).
    // Not a formal std::input_iterator (no default ctor, no operator==),
    // so CopyIter does NOT satisfy std::ranges::input_range. When passed
    // to tpy::construct(), it routes through the __next__() (collect) path.
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

// Src is `C&` for an lvalue source (borrowed) and `C` for a temporary (owned).
template<typename T, typename Src>
auto copy_iter(Src&& src) {
    return CopyIter<T, detail::arg_source_t<Src>>(std::in_place, std::forward<Src>(src));
}

} // namespace tpy
