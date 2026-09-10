# repeat's @overload arms hide the private Optional sentinel, so repeat(obj,
# None) is rejected (CPython raises TypeError for a non-int `times`).
import itertools


def main():
    obj = "x"
    for v in itertools.repeat(obj, None):  # tpyc: error(/No matching overload for repeat/)
        print(v)


main()
