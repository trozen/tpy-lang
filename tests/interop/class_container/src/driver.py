# Shared across the ext-exec and cpy-parity runs: the compiled Box's
# container protocol must behave like a plain Python class with the same
# dunder bodies -- len/getitem/setitem (mutating in place, observed on the
# same object), contains (before/after the mutation), and iteration (list(),
# manual iter()/next(), and a for-loop) all round-trip through CPython's own
# sequence/iterator machinery.
import class_container

b = class_container.Box(10, 20, 30)
print(len(b))                  # 3
print(b[0], b[1], b[2])        # 10 20 30

b[1] = 99                      # mutates the SAME object
print(b[1])                    # 99
print(20 in b, 99 in b)         # False True

vals = list(b)                 # exercises __iter__ + __next__
print(vals)                    # [0, 1, 2]

it = iter(b)
print(next(it), next(it), next(it))   # 0 1 2
try:
    next(it)
    raise AssertionError("expected StopIteration")
except StopIteration:
    print("stop iteration: PASS")

total = 0
for x in b:
    total += x
print(total)                   # 3 (0+1+2)

s = class_container.Slot(7)
print(len(s), s[0])             # 1 7
del s[0]                        # Slot defines __delitem__ -- actual deletion
print(len(s))                   # 0
try:
    s[0]
    raise AssertionError("expected KeyError after deletion")
except KeyError:
    print("slot deleted: PASS")

