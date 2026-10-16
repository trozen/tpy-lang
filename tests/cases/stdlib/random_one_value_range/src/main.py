# random -- a one-value range still draws from the MT stream (CPython's
# _randbelow(1) loops on getrandbits(1) until 0), so later draws stay in sync.
import random
from random import Random


def main() -> None:
    random.seed(1)
    print("randint:", random.randint(5, 5))  # the one-value draw under test
    print("randint next:", random.getrandbits(32))

    random.seed(1)
    print("randrange1:", random.randrange(1))  # the one-value draw under test
    print("randrange1 next:", random.getrandbits(32))

    random.seed(1)
    print("randrange2:", random.randrange(3, 4))  # the one-value draw under test
    print("randrange2 next:", random.getrandbits(32))

    random.seed(1)
    print("randrange3:", random.randrange(2, 9, 10))  # the one-value draw under test
    print("randrange3 next:", random.getrandbits(32))

    random.seed(1)
    print("choice:", random.choice(["only"]))  # the one-value draw under test
    print("choice next:", random.getrandbits(32))

    # Instance method, a seed whose first bits reject so the loop runs.
    r = Random(3)
    for _ in range(4):
        r.randint(7, 7)  # the one-value draw under test
    print("instance next:", r.getrandbits(32))


main()
