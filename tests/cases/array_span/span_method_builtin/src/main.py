# Test __span__() method callable on builtin types and user types (ArrayList)
from tpy import Int32, Array, Span, ReadOnlySpan
from tplib import ArrayList

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

def from_arraylist() -> None:
    al = ArrayList[Int32, 4]()
    al.append(100)
    al.append(200)
    s = al.__span__()
    print(len(s), s[0], s[1])

from_list()
from_array()
from_span()
from_readonly_span()
from_arraylist()
