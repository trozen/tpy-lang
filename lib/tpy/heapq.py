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
#   - merge(*iterables, key=, reverse=) not implemented.
#     Needs a generator-driven n-way iterator-heads heap.
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
