# Cycle members may contain only imports and bare type declarations
# at top level. Anything else (function calls, conditionals, regular
# assignments) is rejected by the Phase 7 reject gate. This test puts
# `a, b` in a cycle and adds a top-level call to b.py to trigger the
# rejection.
import a
print(a.foo())
