from tpy import int32

# Exit value would be 2147483648 (INT32_MAX + 1) — must panic
for i in range(int32(2147483646), int32(2147483647), int32(2)):
    print(i)
