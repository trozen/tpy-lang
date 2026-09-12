# Cross-module generic function import (previously caused linker errors)
from tpy import int32
from helpers import first, length

nums: list[int32] = [int32(10), int32(20), int32(30)]
print(first(nums))
print(length(nums))

words: list[str] = ["hello", "world"]
print(first(words))
print(length(words))
