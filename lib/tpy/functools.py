# functools -- higher-order functions and operations on callable objects
# tpy: cpp_namespace("tpystd::functools")
# tpy: include("<tpy/functools.hpp>")
from tpy import Fn
from tpy.extern import cpp_template

@cpp_template("::tpy::functools_reduce({0}, {1}, {2})")
def reduce[T, U](fn: Fn[[U, T], U], iterable: list[T], initial: U) -> U: ...
