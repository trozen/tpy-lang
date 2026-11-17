/**
 * TurboPython Runtime - Resumable-frame iteration helpers
 *
 * How a generator frame drives the source of a `for` loop it suspends inside:
 * a self-iterator in place, any other source through an iterator the frame
 * owns.
 *
 * Depends on: next_iter.hpp, frame_slot.hpp, dunder.hpp
 */

#pragma once

#include "next_iter.hpp"
#include "frame_slot.hpp"
#include "dunder.hpp"

#include <cstddef>
#include <tuple>
#include <type_traits>
#include <utility>

namespace tpy {

// The element of a source's step result AS IT SITS in the frame's result slot:
// what a pointer into that slot points at, whichever form the source handed
// back (a lent `T&`, a fresh value, a proxy tuple of references). The slot
// keeps the result until the next advance, so the pointer holds for the
// iteration and nothing is copied or converted to take it.
template<typename R>
using step_elem_t = std::remove_reference_t<
    decltype(::tpy::unwrap_ref(*std::declval<R&>()))>;

// Member I of a step element as an unpack target points at it: through the
// normalizing deref the target's bind uses, so a member that is itself a
// reference or a pointer (a proxy tuple's) yields the pointee, not the handle.
template<std::size_t I, typename Tup>
using step_elem_member_t = std::remove_reference_t<decltype(
    ::tpy::unwrap_ref(::tpy::tuple_elem_ref(std::get<I>(std::declval<Tup&>()))))>;

// Resumable-frame `for x in <Iterable>` iterator handling. The source `src` is
// already frame-resident (a captured param, a frame-held local, or a moved-in
// temporary in __for_src); these helpers manage the iterator derived from it,
// stored in the `frame_slot` the frame already declares.
//
// A self-iterator (`is_self_iterator_v`, dunder.hpp) is its own iterator, so
// we keep NO iterator state (the slot stays empty) and drive the source's
// __next__() directly each step, re-read through `src` rather than through a
// stored pointer to it. Its __iter__ is never called here
// (BUGS.md#frame-for-skips-self-iterator-iter). Any other source yields an
// iterator that we own in the slot: a fresh one (a prvalue, e.g. a
// container's native_iterator) is constructed in it (`emplace_from`), and a
// stored one handed back by reference (a *member* iterator -- not self, its
// __iter__ is not idempotent) is COPIED, so the loop advances the copy and
// not the member CPython would advance (BUGS.md#frame-stored-iter-copied).
template<typename Slot, typename S>
void resumable_iter_init(Slot& slot, S& src) {
    if constexpr (!is_self_iterator_v<S>) {
        slot.emplace_from([&]() -> decltype(auto) { return ::tpy::iter_dunder(src); });
    }
}

template<typename Slot, typename S>
decltype(auto) resumable_iter_next(Slot& slot, S& src) {
    if constexpr (is_self_iterator_v<S>) return src.__next__();
    else return (*slot).__next__();
}

// The iterator slot of a loop over an owning element parameter: the type of
// the iterator `handover_iter<A>` returns -- the container's own consuming
// iterator when it was handed over, else the one it lends. The loop steps
// and binds through `resumable_iter_next`, as every loop does.
template<typename A, typename S>
using handover_iter_type_t = std::decay_t<decltype(::tpy::handover_iter<A>(std::declval<S&>()))>;

// `resumable_iter_init` / `resumable_iter_next` for that slot, classified by
// the iterator the slot holds rather than by the source's lvalue face: an
// object handed over is stepped through the consuming iterator it was moved
// into (a record whose only `__iter__` consumes has no lvalue face at all),
// and only a lent self-iterator is driven in place.
template<typename A, typename Slot, typename S>
void resumable_handover_iter_init(Slot& slot, S& src) {
    if constexpr (hands_over_v<A, S>) {
        slot.emplace_from([&]() -> decltype(auto) { return ::tpy::handover_iter<A>(src); });
    } else {
        ::tpy::resumable_iter_init(slot, src);
    }
}

template<typename A, typename Slot, typename S>
decltype(auto) resumable_handover_iter_next(Slot& slot, S& src) {
    if constexpr (hands_over_v<A, S>) return (*slot).__next__();
    else return ::tpy::resumable_iter_next(slot, src);
}

} // namespace tpy
