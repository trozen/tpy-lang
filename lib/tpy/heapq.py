# heapq -- min-heap queue algorithm
# tpy: cpp_namespace("tpystd::heapq")
# tpy: include("<tpy/heapq.hpp>")
from tpy.extern import cpp_template
from tpy import Int32, Comparable, Own, copy

@cpp_template("::tpy::heap_push({0}, {1})")
def heappush[T: Comparable](heap: list[T], item: T) -> None: ...

@cpp_template("::tpy::heap_pop({0})")
def heappop[T: Comparable](heap: list[T]) -> Own[T]: ...

@cpp_template("::tpy::heap_heapify({0})")
def heapify[T: Comparable](heap: list[T]) -> None: ...

@cpp_template("::tpy::heap_replace({0}, {1})")
def heapreplace[T: Comparable](heap: list[T], item: T) -> Own[T]: ...

@cpp_template("::tpy::heap_pushpop({0}, {1})")
def heappushpop[T: Comparable](heap: list[T], item: T) -> Own[T]: ...

def nsmallest[T: Comparable](n: Int32, iterable: list[T]) -> Own[list[T]]:
    result = list(iterable)
    heapify(result)
    out: list[T] = []
    i: Int32 = 0
    size = Int32(len(result))
    m = n if n < size else size
    while i < m:
        out.append(heappop(result))
        i += 1
    return out

def nlargest[T: Comparable](n: Int32, iterable: list[T]) -> Own[list[T]]:
    # Sort ascending, reverse, take first n -- avoids in-place swap over generic T
    result = sorted(iterable)
    result.reverse()
    out: list[T] = []
    k: Int32 = 0
    size = Int32(len(result))
    m = n if n < size else size
    while k < m:
        out.append(copy(result[k]))
        k += 1
    return out
