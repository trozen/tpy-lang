# heapq -- min-heap priority queue over list[T], following CPython's algorithm.
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
#     Blocked on variadic-in-method-call codegen (same gate as math.hypot
#     variadic) plus a generator-driven n-way iterator-heads heap.
#
# Language-level blockers that limit heapq coverage:
#   - tuple[T1, T2, ...] does not satisfy Comparable even when all Ti are
#     Comparable (built-in < works, but no __lt__ method). Blocks the
#     canonical `list[tuple[priority, payload]]` priority-queue pattern.
#     TODO in the compiler: lift tuple's built-in ordering into Comparable
#     protocol conformance.
#   - Generic list[T].pop() produces invalid C++ when T is a reference type
#     (val_or_ref_t<T> = T& can't bind to pop_back's rvalue return). Blocks
#     ref-type heaps; also affects the existing generic_stack test when
#     instantiated with a user class. Needs codegen fix for generic-T
#     returns that take ownership.
#
# Codegen perf gap affecting this module (see TODO.md "Missed optimizations" and
# `docs/IR_DESIGN.md` "Open Questions" item 8):
#   - Generic-T params route through the trait `param_val_or_ref_t<T>` which is
#     keyed on the C++ storage type, so `heappush[str]` lands on `const
#     std::string&` instead of `std::string_view` (the trait can't distinguish
#     TPy `str` from TPy `String`, since both share `std::string` storage).
#     Every string literal push materializes a `std::string` at the call site
#     (SSO covers short literals). Direct `def f(s: str)` params lower to
#     string_view correctly; only generics miss it. The proper fix needs
#     TPy-type-aware generic codegen (descriptor template parameters) and is
#     deferred to the IR migration -- see IR_DESIGN.md.
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

def heappush[T: Comparable](heap: list[T], item: T) -> None:
    heap.append(copy(item))
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

def heapreplace[T: Comparable](heap: list[T], item: T) -> Own[T]:
    returnitem: T = copy(heap[0])
    heap[0] = copy(item)
    _siftup(heap, 0)
    return returnitem

def heappushpop[T: Comparable](heap: list[T], item: T) -> Own[T]:
    if len(heap) > 0 and heap[0] < item:
        result: T = copy(heap[0])
        heap[0] = copy(item)
        _siftup(heap, 0)
        return result
    return copy(item)

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
