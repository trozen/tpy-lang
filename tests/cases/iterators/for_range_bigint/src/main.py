# Big integers that exceed int32 range -- forces BigInt path
base = 1 << 100  # tpyc: warning(/outside default int32 range/)

# 1. BigInt range with start/stop
for i in range(base, base + 5):
    print(i)

# 2. BigInt range with step
for i in range(base, base + 10, 3):
    print(i)

# 3. Negative BigInt step
for i in range(base + 4, base - 1, -1):
    print(i)

# 4. Empty BigInt range
for i in range(base + 5, base):
    print(i)
print("done")
