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

#include <type_traits>
#include <utility>

namespace tpy {

// The element of a source's step result AS IT SITS in the frame's result slot:
// what a pointer into that slot points at, whichever form the source handed
// back (a lent `T&`, a fresh value, a proxy tuple of references). The slot
// keeps the result until the next advance, so the pointer holds for the
// iteration and nothing is copied or converted to take it.
template<typename S>
using for_step_elem_t = std::remove_reference_t<
    decltype(::tpy::unwrap_ref(*std::declval<iter_result_t<S>&>()))>;

// Resumable-frame `for x in <Iterable>` iterator handling. The source `src` is
// already frame-resident (a captured param, a frame-held local, or a moved-in
// temporary in __for_src); these helpers manage the iterator derived from it,
// stored in the `frame_slot` the frame already declares.
//
// A self-iterator (`is_self_iterator_v`, dunder.hpp) is its own iterator, so
// we keep NO iterator state (the slot stays empty) and drive the source's
// __next__() directly each step, re-read through `src` so a frame move can't
// dangle a stored self-pointer. Its __iter__ is never called here
// (BUGS.md#frame-for-skips-self-iterator-iter). Any other source yields an independent
// iterator (a prvalue, e.g. a container's native_iterator) that we own in the
// slot as before. A source whose __iter__ returns a reference to a *member*
// iterator is intentionally NOT treated as self (its __iter__ is not idempotent)
// and takes the owned path.
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
