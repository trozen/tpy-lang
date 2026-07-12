# Shared across the ext-exec and cpy-parity runs: the compiled container
# marshallers must reproduce what the plain Python source computes. Only
# value-in/value-out round-trips appear here (no aliasing reliance) so the
# compiled .so (copy-in) and the interpreted source (aliasing) agree; the
# copy-semantics divergence lives in ext_checks.py.
import containers as m

print(m.sum_list([1, 2, 3, 100]))            # 106
print(m.sum_list([]))                         # 0 (empty)
print(m.doubled([1, 2, 3]))                   # [2, 4, 6]
print(m.doubled([]))                          # []
print(m.total_len(["ab", "", "cde"]))         # 5
print(m.shout(["hi", "yo"]))                  # ['hi!', 'yo!']
print(m.dict_sum({"a": 1, "b": 2, "c": 3}))   # 6
print(m.dict_sum({}))                         # 0
print(m.histogram(["a", "a", "b", "c", "c", "c"]))  # insertion order preserved
print(m.set_size({1, 2, 3, 3, 2}))            # 3
print(sorted(m.to_set([3, 1, 1, 2, 3])))      # [1, 2, 3]
print(m.make_pair(3))                         # (3, 'n=3')
print(m.swap((7, "k")))                       # ('k', 7)
print(m.flatten({"x": [1, 2], "y": [3], "z": []}))  # [1, 2, 3]
print(m.byte_lengths([b"", b"ab", b"cdef"]))  # [0, 2, 4]
# keyword argument over a container param
print(m.sum_list(xs=[10, 20]))                # 30
