# Cycle peer re-exports another cycle peer's record. main pulls
# AType from a; AType.make_b() produces a BType, which a re-exports
# transitively from b through the cycle. Phase 5 enables this:
# universal re-export plus cycle-aware `using` suppression keeps the
# C++ build green.
from a import AType

a = AType()
b = a.make_b()
print(b.use_a(a))
