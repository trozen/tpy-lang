# A generator-factory rvalue at a PLAIN (non-generator) callee slot: the argument
# temp is keyed on both sides being generator-like, so this has no hoist.
from typing import Iterator
from tpy import Int32
import itertools


def first_or(it: Iterator[int], d: Int32) -> Int32:
    for x in it:
        return x
    return d


def main() -> None:
    print(first_or(itertools.count(), 9))  # tpyc: error(/method.marker.module.plain/)


main()
