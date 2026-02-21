from tpy import Int32

# Exit value would be -2147483649 (INT32_MIN - 1) — must panic
for i in range(Int32(-2147483647), Int32(-2147483648), Int32(-2)):
    print(i)
