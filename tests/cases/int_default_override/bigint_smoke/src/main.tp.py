# BigInt-default profile should keep unannotated integer arithmetic in BigInt.
x = 2 ** 64
print(x)

# Large shifts stay precise.
y = 1 << 100
print(y)

# Mixed unannotated literals remain BigInt.
z = 5
print(z + 7)

# range() should use BigInt loop variable.
for i in range(3):
    print(i)

# List literal elements should be BigInt.
items = [10, 20, 30]
print(items[0])
