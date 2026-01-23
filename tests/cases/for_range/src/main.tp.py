from tpy import Int32

def print_range(start: Int32, end: Int32) -> None:
    for i in range(start, end):
        print(i)

def sum_range(n: Int32) -> Int32:
    total: Int32 = 0
    for i in range(n):
        total += i
    return total

# Single-arg range (0 to n)
for i in range(5):
    print(i)

# Two-arg range (start to end)
print_range(10, 15)

# Sum using range
print(sum_range(10))
