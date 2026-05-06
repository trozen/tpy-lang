# 3-module SCC: a -> b -> c -> a. Tarjan finds the SCC, the
# alphabetical ordering inside the SCC drives sub-phase iteration,
# and skeleton pre-registration unblocks every cross-cycle
# `from X import Y`. Each module emits a fwd.hpp.
from a import call_b

def main() -> None:
    print(call_b())

main()
