# The module must bind a generator-expression walrus target before the
# statement; one it binds only after the expression may not be assigned.

# The subject: `late` is bound by the later `late = 0`, not before the frame.
found = any((late := x) > 1 for x in [1, 2])  # tpyc: error(/variable 'late' may not be assigned at this point/)
late = 0
print(found, late)
