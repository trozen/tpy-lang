# `is` between two operands of a generic type parameter is not supported yet
# (BUGS.md#identity-generic-param-rejects).
def same[T](a: T, b: T) -> bool:
    return a is b  # tpyc: error(/'is' \/ 'is not' can only compare objects of a known type/)


print(same(1, 2))
