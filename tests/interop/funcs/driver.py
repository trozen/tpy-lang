# Shared across the ext-exec and cpy-parity runs: only values that match
# between the compiled extension and the TPy source under CPython (all fit the
# declared types). Error cases that DO diverge (TPy Int64 is bounded; CPython
# int is not) live in ext_checks.py.
import funcs

print(funcs.answer())
print(funcs.add(2, 3))
print(funcs.add(-100, 100))
print(funcs.add(0, 0))
print(funcs.add(2**63 - 1, 0))    # INT64_MAX, fits
print(funcs.add(-(2**63), 0))     # INT64_MIN, fits
print(funcs.big_square(0))
print(funcs.big_square(12345))
print(funcs.big_square(10**20))   # beyond int64 -> hex round-trip both ways
print(funcs.big_square(-(10**25)))
print(funcs.negate(0))
print(funcs.negate(10**30))
print(funcs.negate(-7))
