from tpy import Int32

# Empty ranges -- body should never execute
for i in range(0):
    print(i)

for i in range(5, 5):
    print(i)

for i in range(5, 0):
    print(i)

for i in range(0, 10, -1):
    print(i)

# Single element
for i in range(1):
    print(i)

for i in range(3, 4):
    print(i)

# Negative range
for i in range(-3, 0):
    print(i)

# Large step that overshoots
for i in range(0, 10, 100):
    print(i)

for i in range(10, 0, -100):
    print(i)

# Compound expression -- constant-folded to Int32
for i in range(1 + 2):
    print(i)

print("done")
