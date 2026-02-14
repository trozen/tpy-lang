from tpy import Int32, Int8, UInt8

# --- Non-panic boundary cases ---

# Large step, one iteration (step > range width)
for i in range(Int32(0), Int32(2147483647), Int32(2147483647)):
    print(i)

# Step exactly divides range: exit value = stop = INT32_MAX
count: Int32 = Int32(0)
for i in range(Int32(1), Int32(2147483647), Int32(2)):
    count += Int32(1)
print(count)  # 1073741823

# Negative step at INT32_MIN boundary, step=-1
for i in range(Int32(-2147483647), Int32(-2147483648), Int32(-1)):
    print(i)

# Large negative step, one iteration
for i in range(Int32(0), Int32(-1), Int32(-2147483648)):
    print(i)

# One iteration near max, step > 1
for i in range(Int32(0), Int32(1), Int32(2147483647)):
    print(i)

# Empty range (start >= stop with positive step) — no check needed
for i in range(Int32(2147483647), Int32(0), Int32(2)):
    print(i)

# Int8: step divides evenly, exit = stop = 127
for i in range(Int8(0), Int8(127), Int8(127)):
    print(i)

# Int8: negative step at boundary
for i in range(Int8(0), Int8(-1), Int8(-128)):
    print(i)

# UInt8: step divides evenly, exit = stop = 250
for i in range(UInt8(0), UInt8(250), UInt8(50)):
    print(i)

print("all safe cases done")
