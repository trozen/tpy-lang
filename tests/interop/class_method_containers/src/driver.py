# Shared across the ext-exec and cpy-parity runs: value-in/value-out
# round-trips only (no aliasing reliance), so the compiled .so (copy-in) and
# the interpreted source (aliasing) agree; the copy-semantics divergences live
# in ext_checks.py.
import array
import class_method_containers as m

s = m.Stats([1, 2, 3])
print(s.total)                                        # 6
print(m.Stats([]).total)                              # 0 (empty)
print(s.add_dict({"a": 10, "b": 20}))                 # 36
s.add_span(array.array("q", [100, 200]))
print(s.total)                                        # 336
print(s.span_sum(array.array("q", [7, 8])))           # 15
print(s.span_sum(memoryview(array.array("q", [1, 2]))))  # 3
print(s.scaled([4, 5], 3))                            # [12, 15]
print(s.scaled([], 3))                                # []
print(sorted(s.uniq([3, 1, 3, 2])))                   # [1, 2, 3]
print(s.snapshot())                                   # (336, 'total')
print(s.flatten({"x": [1, 2], "y": [], "z": [3]}))    # [1, 2, 3]
print(s.absorb([1, 2], 9))                            # 3 (the copy grew)
print(s.same([7, 8]))                                 # [7, 8]
# keyword arguments over container method and __init__ params
print(s.scaled(xs=[1], k=5))                          # [5]
print(m.Stats(seed=[10, 20]).total)                   # 30
