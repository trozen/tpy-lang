# Cross-module generic function import (previously caused linker errors)
from tpy import Int32
from helpers import first, length

nums: list[Int32] = [Int32(10), Int32(20), Int32(30)]
print(first(nums))
print(length(nums))

words: list[str] = ["hello", "world"]
print(first(words))
print(length(words))
