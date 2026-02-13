from tpy import Int32

# range(stop) - basic
for i in range(5):
    print(i)

# range(start, stop)
for i in range(2, 6):
    print(i)

# range(start, stop, step) - step of 2
for i in range(0, 10, 2):
    print(i)

# range(start, stop, step) - negative step (countdown)
for i in range(10, 0, -2):
    print(i)

# range with Int32 variables
def sum_range_step(start: Int32, stop: Int32, step: Int32) -> Int32:
    total: Int32 = 0
    for i in range(start, stop, step):
        total += i
    return total

print(sum_range_step(0, 10, 3))  # 0 + 3 + 6 + 9 = 18

# range with BigInt args (converted to Int32)
n = 5
for i in range(n):
    print(i)
