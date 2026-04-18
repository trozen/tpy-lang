# Tests for extended random module: randint, uniform, choice, shuffle, gauss
import random
from tpy import Int32

def main() -> None:
    random.seed(Int32(0))

    # randint: result in [a, b] inclusive
    n = random.randint(1, 10)
    print(n >= 1)
    print(n <= 10)

    # uniform: result in [a, b)
    u = random.uniform(0.5, 1.5)
    print(u >= 0.5)
    print(u < 1.5)

    # gauss: check it produces a float (not NaN/inf)
    g = random.gauss(0.0, 1.0)
    print(g > -20.0)
    print(g < 20.0)

    # expovariate: always positive
    e = random.expovariate(1.0)
    print(e > 0.0)

    # choice: result must be an element of the list
    items: list[Int32] = [10, 20, 30, 40, 50]
    c = random.choice(items)
    found = False
    for x in items:
        if x == c:
            found = True
    print(found)

    # shuffle: all elements still present after shuffle
    lst: list[Int32] = [1, 2, 3, 4, 5]
    random.shuffle(lst)
    total: Int32 = 0
    for x in lst:
        total += x
    print(total == 15)

main()
