# bisect -- array bisection algorithms
# tpy: cpp_namespace("tpystd::bisect")
from tpy import Int32, Comparable, copy

def bisect_left[T: Comparable](a: list[T], x: T) -> Int32:
    lo: Int32 = 0
    hi: Int32 = Int32(len(a))
    while lo < hi:
        mid: Int32 = (lo + hi) // 2
        if a[mid] < x:
            lo = mid + 1
        else:
            hi = mid
    return lo

def bisect_right[T: Comparable](a: list[T], x: T) -> Int32:
    lo: Int32 = 0
    hi: Int32 = Int32(len(a))
    while lo < hi:
        mid: Int32 = (lo + hi) // 2
        if x < a[mid]:
            hi = mid
        else:
            lo = mid + 1
    return lo

def insort_left[T: Comparable](a: list[T], x: T) -> None:
    i: Int32 = bisect_left(a, x)
    a.insert(i, copy(x))

def insort_right[T: Comparable](a: list[T], x: T) -> None:
    i: Int32 = bisect_right(a, x)
    a.insert(i, copy(x))

def bisect[T: Comparable](a: list[T], x: T) -> Int32:
    return bisect_right(a, x)

def insort[T: Comparable](a: list[T], x: T) -> None:
    insort_right(a, x)
