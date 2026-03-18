# Comprehension loop variable shadows same-named non-value-type global
from tpy import Array

x = [10, 20, 30]

# List comprehension
r1 = [len(x) for x in ["a", "bb", "ccc"]]

# Set comprehension
r2 = {len(x) for x in ["a", "bb", "ccc"]}

# Dict comprehension
r3 = {x: len(x) for x in ["a", "bb", "ccc"]}

# Generator expression
r4 = list(len(x) for x in ["a", "bb", "ccc"])

# With filter condition
r5 = [len(x) for x in ["a", "bb", "ccc", "dd"] if len(x) > 1]

# Range-based (original bug repro from TODO)
r6 = [x * x for x in range(5)]

# Array comprehension path (range-based, promotes to std::array)
r7: Array[int, 5] = [x * x for x in range(5)]

def main() -> None:
    print(x)
    print(r1)
    print(r2)
    print(r3)
    print(r4)
    print(r5)
    print(r6)
    print(r7)

main()
