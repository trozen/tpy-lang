# int64 default: in-range literals should stay fixed-int.
from tpy import dispatch, int32, int64
a = 2 ** 40
print(a)

# Still supports regular arithmetic in fixed-int mode.
b = 10
print(a + b)

# Out-of-range literal should promote to BigInt with warning.
c = 2 ** 70  # tpyc: warning(/outside default int64 range/)
print(c)

# range() should use int64 loop variable.
for i in range(3):
    print(i)

# List literal elements should be int64.
items = [10, 20, 30]
print(items[0])


# An operator dunder set ranks an int literal at the default width, and the
# literal is pinned to the picked variant's parameter (int64 here).
class Widths:
    def __init__(self) -> None:
        pass

    @dispatch
    def __add__(self, x: int64) -> int32:
        return 64

    @dispatch
    def __add__(self, x: int32) -> int32:
        return 32


w = Widths()
print("dunder:", w + 3)  # tpyc: ok
