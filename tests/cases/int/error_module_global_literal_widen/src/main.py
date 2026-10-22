# An unannotated module global keeps the type of its first typed binding: an
# int literal it cannot hold is refused too (CPython would rebind it to the
# literal), with the annotation that gives the global the wider type.
from tpy import int32

x = int32(1)
for i in range(2):
    t = (x, 1)
    # 5000000000 is outside int32: a wider store
    x = 5000000000  # tpyc: error(/annotate its first binding: x: int = /)
print(t, x)
