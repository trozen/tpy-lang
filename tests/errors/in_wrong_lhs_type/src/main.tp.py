# Test that `in` operator rejects wrong LHS types for string containers

x: Bool = 1 in "abc"  # tpyc: error(/Cannot check.*membership in str/)
