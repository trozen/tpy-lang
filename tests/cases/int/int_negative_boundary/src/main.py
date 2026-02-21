# Int32 boundary literals: values at exactly min/max stay Int32,
# values one beyond promote to BigInt with a warning.

# Exactly Int32 min -- should stay Int32
a = -2147483648
print(a)

# One below Int32 min -- should promote to BigInt with warning
b = -2147483649  # tpyc: warning(/outside default Int32 range/)
print(b)

# Exactly Int32 max -- should stay Int32
c = 2147483647
print(c)

# One above Int32 max -- should promote to BigInt with warning
d = 2147483648  # tpyc: warning(/outside default Int32 range/)
print(d)
