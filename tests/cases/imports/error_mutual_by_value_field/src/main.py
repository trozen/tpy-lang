# Cycle members reference each other by-value through a ValueType
# field. The completeness-graph reject gate (Phase 16, mutual
# imports) catches this and produces a structured diagnostic before
# the C++ build hits a complete-type error.
from a import A
from tpy import int32

def main() -> int32:
    return 0

main()
