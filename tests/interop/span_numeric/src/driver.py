# Shared across the ext-exec and cpy-parity runs: value-in/value-out
# round-trips only (no aliasing reliance), so the compiled .so (copy-in) and
# the interpreted source (aliasing) agree. scale_in_place's copy-semantics
# divergence lives in ext_checks.py.
import array
import span_numeric as m

print(m.sum_floats(array.array("d", [1.0, 2.0, 3.5])))              # 6.5
print(m.sum_floats(array.array("d", [])))                            # 0.0
print(m.sum_floats(memoryview(array.array("d", [1.5, 2.5]))))        # 4.0
print(m.sum_int32(array.array("i", [1, 2, 3, 100])))                  # 106
print(m.sum_int32(array.array("i", [])))                              # 0
print(m.sum_uint8(bytes([1, 2, 3, 250])))                             # 256
print(m.sum_uint8(bytearray([10, 20])))                               # 30
print(m.sum_uint8(b""))                                               # 0
print(m.peek_int32(array.array("i", [5, 10, 15])))                    # 30
print(m.peek_int32(memoryview(array.array("i", [1, 2, 3]))))          # 6
