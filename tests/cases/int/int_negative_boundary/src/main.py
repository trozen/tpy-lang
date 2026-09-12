# int32 boundary literals: values at exactly min/max stay int32,
# values one beyond promote to BigInt with a warning.

# Exactly int32 min -- should stay int32
a = -2147483648
print(a)

# One below int32 min -- should promote to BigInt with warning
b = -2147483649  # tpyc: warning(/outside default int32 range/)
print(b)

# Exactly int32 max -- should stay int32
c = 2147483647
print(c)

# One above int32 max -- should promote to BigInt with warning
d = 2147483648  # tpyc: warning(/outside default int32 range/)
print(d)
