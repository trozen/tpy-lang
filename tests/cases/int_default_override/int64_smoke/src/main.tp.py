# Int64 default: in-range literals should stay fixed-int.
a = 2 ** 40
print(a)

# Still supports regular arithmetic in fixed-int mode.
b = 10
print(a + b)

# Out-of-range literal should promote to BigInt with warning.
c = 2 ** 70  # tpyc: warning(/outside default Int64 range/)
print(c)

# range() should use Int64 loop variable.
for i in range(3):
    print(i)

# List literal elements should be Int64.
items = [10, 20, 30]
print(items[0])
