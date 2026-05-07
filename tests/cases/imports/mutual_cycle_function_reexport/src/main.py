# Cycle peer re-exports another cycle peer's function. main pulls
# `bee` via `a`, but `bee` is defined in `b` (cycle peer of `a`).
# The `using ::tpyapp::b::bee;` line in a.hpp is suppressed by Phase 5's
# cycle-aware codegen (functions aren't declared in `<peer>_fwd.hpp`),
# so consumer codegen must qualify directly to b's namespace and pull
# b.hpp into reach. Regression test for the cycle function re-export
# bug Codex flagged in the post-Phase-8 review.
from a import bee, aye

print(bee())
print(aye())
