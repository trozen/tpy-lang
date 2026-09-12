from tpy import int32, int8, uint8

# --- Non-panic boundary cases ---

# Large step, one iteration (step > range width)
for i in range(int32(0), int32(2147483647), int32(2147483647)):
    print(i)

# Step exactly divides range: exit value = stop = INT32_MAX
count: int32 = int32(0)
for i in range(int32(1), int32(2147483647), int32(2)):
    count += int32(1)
print(count)  # 1073741823

# Negative step at INT32_MIN boundary, step=-1
for i in range(int32(-2147483647), int32(-2147483648), int32(-1)):
    print(i)

# Large negative step, one iteration
for i in range(int32(0), int32(-1), int32(-2147483648)):
    print(i)

# One iteration near max, step > 1
for i in range(int32(0), int32(1), int32(2147483647)):
    print(i)

# Empty range (start >= stop with positive step) — no check needed
for i in range(int32(2147483647), int32(0), int32(2)):
    print(i)

# int8: step divides evenly, exit = stop = 127
for i in range(int8(0), int8(127), int8(127)):
    print(i)

# int8: negative step at boundary
for i in range(int8(0), int8(-1), int8(-128)):
    print(i)

# uint8: step divides evenly, exit = stop = 250
for i in range(uint8(0), uint8(250), uint8(50)):
    print(i)

print("all safe cases done")
