# random.random() and random.seed()
import random
from tpy import int32

def main() -> None:
    random.seed(int32(42))
    a = random.random()
    b = random.random()
    # Values should be in [0, 1)
    print(a >= 0.0)  # True
    print(a < 1.0)   # True
    print(b >= 0.0)  # True
    print(b < 1.0)   # True
    # Same seed should give same sequence
    random.seed(int32(42))
    c = random.random()
    print(a == c)  # True

main()
