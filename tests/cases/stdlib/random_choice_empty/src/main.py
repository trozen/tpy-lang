# random.choice on an empty sequence raises IndexError (CPython parity),
# via both the module-level function and the Random instance method.
import random
from tpy import Int32

def main() -> None:
    empty: list[Int32] = []
    try:
        x = random.choice(empty)
        print(x)
    except IndexError as e:
        print("IndexError:", str(e))
    rng = random.Random()
    try:
        y = rng.choice(empty)
        print(y)
    except IndexError as e:
        print("IndexError:", str(e))

main()
