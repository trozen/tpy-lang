# A list index converts as a call argument does: an int64 or int key narrows
# into the int32 index parameter with a range check, at the subscript and at
# its explicit dunder call alike (read, store, augmented store, del).
from tpy import int32, int64


def main() -> None:
    xs: list[int32] = [1, 2, 3, 4]
    i: int64 = 1
    big: int = 2
    # read: the subscript and the explicit call take the same checked narrow
    print("read", xs[i], xs.__getitem__(i))  # tpyc: ok
    print("read_big", xs[big], xs.__getitem__(big))  # tpyc: ok
    # store and augmented store
    xs[i] = 20  # tpyc: ok
    xs.__setitem__(big, 30)
    xs[big] += 5  # tpyc: ok
    print("store", xs)
    # del
    del xs[i]  # tpyc: ok
    xs.__delitem__(big)
    print("del", xs)


main()
