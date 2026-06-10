# heapq -- min-heap priority queue over list[T], following CPython's algorithm.
#
# Constraint: T must be copyable. `_siftup` / `_siftdown` stash and shift
# elements via `copy(heap[i])`, so `@nocopy` element types fail at C++
# compile time with a deleted-copy-constructor error. The diagnostic gap
# is tracked in BUGS.md; wrap a `@nocopy` payload in `Rc[T]` or `Box[T]`
# if you need shared ownership in a heap.
#
# Gaps vs. CPython, tracked in STDLIB_ROADMAP.md:
#   - nsmallest/nlargest take list[T] instead of Iterable[T]
#     (same list-literal-vs-protocol conformance gap as math.prod/fsum;
#     TODO: switch to Iterable[T] once that blocker is resolved).
#   - nlargest is sort-based (O(n log n)); CPython uses a size-k min-heap
#     (O(n log k)). TODO: port the size-k-heap impl once it matters.
#   - No `key=` arg on nsmallest/nlargest.
#     TODO: thread `Callable[[T], K: Comparable]` through, matches CPython.
#   - merge(*iterables) merges pre-sorted list[T] inputs lazily.
#     Divergences from CPython: inputs must be finite list[T], not
#     arbitrary iterables (heterogeneous iterables need dynamic-iterator
#     erasure + protocol varargs); O(inputs) per element via a cursor
#     scan rather than CPython's O(log inputs) heap; key= / reverse= are
#     absent (key= is blocked on the readonly-through-generic-T callable
#     gap in BUGS.md, reverse= is a cheap follow-up); yields `Own[T]`
#     (owned copies), not the source objects -- CPython preserves identity;
#     and bare merge() needs an explicit type (heapq.merge[T]()) since T
#     is uninferable.
#     TODO(perf): the cursor scan is O(inputs) per element. A custom
#     index-heap (heap array of Int32 list-indices, sift comparing via
#     iterables[li][cursor[li]]) would reach O(log inputs) without storing
#     a per-entry copy/pointer -- the generic heappush/heappop can't, since
#     its comparator can't index back into the source lists. Port once
#     many-input merges matter; the linear scan is fine for small input
#     counts.
#
# Codegen perf gap affecting this module (see TODO.md "Missed optimizations" and
# `docs/IR_DESIGN.md` "Open Questions" item 8):
#   - For `T = str`, the heap's element storage is `std::string` (TPy `str` and
#     TPy `String` share `std::string` storage and the trait machinery can't
#     distinguish them). Push (`item: Own[T]`) lowers to `std::string&&`, so a
#     literal `heappush(words, "cherry")` materializes a `std::string` at the
#     call site instead of passing through a `std::string_view`. SSO covers
#     short literals. No fix planned -- the perf gap is small in practice and
#     every idiomatic alternative pays a real cost. See IR_DESIGN.md for the
#     design landscape.
# tpy: cpp_namespace("tpystd::heapq")
from tpy import Int32, Comparable, Own, copy
from typing import Iterator

def _siftdown[T: Comparable](heap: list[T], startpos: Int32, pos: Int32) -> None:
    newitem: T = copy(heap[pos])
    while pos > startpos:
        parentpos: Int32 = (pos - 1) >> 1
        if newitem < heap[parentpos]:
            heap[pos] = copy(heap[parentpos])
            pos = parentpos
            continue
        break
    heap[pos] = newitem

def _siftup[T: Comparable](heap: list[T], pos: Int32) -> None:
    endpos: Int32 = Int32(len(heap))
    startpos: Int32 = pos
    newitem: T = copy(heap[pos])
    childpos: Int32 = 2 * pos + 1
    while childpos < endpos:
        rightpos: Int32 = childpos + 1
        if rightpos < endpos and not heap[childpos] < heap[rightpos]:
            childpos = rightpos
        heap[pos] = copy(heap[childpos])
        pos = childpos
        childpos = 2 * pos + 1
    heap[pos] = newitem
    _siftdown(heap, startpos, pos)

def heappush[T: Comparable](heap: list[T], item: Own[T]) -> None:
    heap.append(item)
    _siftdown(heap, 0, Int32(len(heap)) - 1)

def heappop[T: Comparable](heap: list[T]) -> Own[T]:
    lastelt = heap.pop()
    if len(heap) == 0:
        return lastelt
    returnitem = copy(heap[0])
    heap[0] = copy(lastelt)
    _siftup(heap, 0)
    return returnitem

def heapify[T: Comparable](x: list[T]) -> None:
    n: Int32 = Int32(len(x))
    i: Int32 = n // 2 - 1
    while i >= 0:
        _siftup(x, i)
        i -= 1

def heapreplace[T: Comparable](heap: list[T], item: Own[T]) -> Own[T]:
    returnitem: T = copy(heap[0])
    heap[0] = item
    _siftup(heap, 0)
    return returnitem

def heappushpop[T: Comparable](heap: list[T], item: Own[T]) -> Own[T]:
    if len(heap) > 0 and heap[0] < item:
        result: T = copy(heap[0])
        heap[0] = item
        _siftup(heap, 0)
        return result
    return item

def nsmallest[T: Comparable](n: Int32, a: list[T]) -> Own[list[T]]:
    h: list[T] = a.copy()
    heapify(h)
    result: list[T] = []
    total: Int32 = Int32(len(h))
    k: Int32 = n if n < total else total
    i: Int32 = 0
    while i < k:
        result.append(heappop(h))
        i += 1
    return result

def nlargest[T: Comparable](n: Int32, a: list[T]) -> Own[list[T]]:
    # Sort-based; a size-k heap variant (O(n log k)) is a perf follow-up.
    h: list[T] = a.copy()
    h.sort()
    h.reverse()
    result: list[T] = []
    total: Int32 = Int32(len(h))
    k: Int32 = n if n < total else total
    i: Int32 = 0
    while i < k:
        result.append(copy(h[i]))
        i += 1
    return result

# One cursor per input; each step scans the live heads and emits the smallest,
# advancing only that input's cursor. O(inputs) per element vs CPython's
# O(log inputs) heap -- merging many streams is rare, so the linear scan is the
# simpler tradeoff (and a value-ordered heap can't help here: its comparator
# can't reach back into the source lists, so it would have to store a copy or
# an unsafe pointer per entry). Strict `<` makes the lowest-indexed input win
# ties, so equal elements emit in input order -- stable, like CPython.
#
# Inputs are compared in place by index, never copied into merge state. The
# yield is an explicit `copy()` into an owned `Own[T]` slot, so the consumer
# owns each element (it can store it without an implicit-copy warning) -- which
# is why merge yields copies, not the source objects, for reference-type T (the
# copy-not-alias divergence in the header). `iterables[best]` is re-indexed
# inline rather than bound to a local: a non-const local alias of the const
# vararg element won't compile.
def merge[T: Comparable](*iterables: list[T]) -> Iterator[Own[T]]:
    cursors: list[Int32] = []
    for src in iterables:
        cursors.append(0)
    while True:
        best: Int32 = -1
        i: Int32 = 0
        for src in iterables:
            c: Int32 = cursors[i]
            if c < len(src) and (best < 0 or src[c] < iterables[best][cursors[best]]):
                best = i
            i += 1
        if best < 0:
            break
        yield copy(iterables[best][cursors[best]])
        cursors[best] = cursors[best] + 1
