# Test __span__() method callable on builtin types (list, Array, Span, ReadOnlySpan, StaticList)
from tpy import Int32, Array, Span, ReadOnlySpan, StaticList

def from_list() -> None:
    data: list[Int32] = [1, 2, 3]
    s = data.__span__()
    print(len(s), s[0], s[1], s[2])

def from_array() -> None:
    a: Array[Int32, 3] = [10, 20, 30]
    s = a.__span__()
    print(len(s), s[0], s[1], s[2])

def from_span() -> None:
    a: Array[Int32, 3] = [4, 5, 6]
    sp: Span[Int32] = a
    s = sp.__span__()
    print(len(s), s[0], s[1], s[2])

def from_readonly_span() -> None:
    a: Array[Int32, 3] = [7, 8, 9]
    ro: ReadOnlySpan[Int32] = a
    s = ro.__span__()
    print(len(s), s[0], s[1], s[2])

def from_static_list() -> None:
    a: Array[Int32, 2] = [100, 200]
    sl: StaticList[Int32, 4] = StaticList[Int32, 4](a)
    s = sl.__span__()
    print(len(s), s[0], s[1])

from_list()
from_array()
from_span()
from_readonly_span()
from_static_list()
