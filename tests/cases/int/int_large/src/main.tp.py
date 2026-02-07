def factorial(n: int) -> int:
    if n <= 1:
        return 1
    return n * factorial(n - 1)


# 20! fits in 63-bit small int
print(factorial(20))

# 25! exceeds 63 bits - uses GMP
print(factorial(25))

# 50! is huge - only GMP can handle
print(factorial(50))

# 100! - truly arbitrary precision
print(factorial(100))
