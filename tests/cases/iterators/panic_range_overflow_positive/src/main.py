from tpy import Int32

# Exit value would be 2147483648 (INT32_MAX + 1) — must panic
for i in range(Int32(2147483646), Int32(2147483647), Int32(2)):
    print(i)
