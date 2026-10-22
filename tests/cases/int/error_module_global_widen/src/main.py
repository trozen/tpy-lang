# An unannotated module global keeps the type of its first typed binding: a
# wider store is refused (CPython would rebind it to the wider value), with
# the annotation that gives the global the wider type.
from tpy import uint8

w8 = uint8(255)
for i in range(2):
    t = (w8, 1)
    # the promoted sum is int32, wider than the uint8 global
    w8 = w8 + 1  # tpyc: error(/annotate its first binding: w8: int32 = /)
print(t, w8)
