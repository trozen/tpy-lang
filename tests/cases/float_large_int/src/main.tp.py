# Test converting large floats to int (requires GMP, not int64)
a = int(1e18)  # Within int64 range
b = int(1e50)  # Way beyond int64 range
c = int(-1e50)  # Negative large value

print(a)
print(b)
print(c)

# Also test that truncation toward zero works
d = int(1.9e20)
e = int(-1.9e20)
print(d)
print(e)
