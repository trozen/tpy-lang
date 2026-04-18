# functools -- higher-order functions and operations on callable objects
# tpy: cpp_namespace("tpystd::functools")
from tpy import Fn

def reduce[T, U](fn: Fn[[U, T], U], iterable: list[T], initial: U) -> U:
    result = initial
    for item in iterable:
        result = fn(result, item)
    return result
