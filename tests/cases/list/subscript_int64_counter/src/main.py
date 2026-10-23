# Under --default-int=int64 an unannotated loop counter is an int64: the list
# index narrows it into its int32 parameter with a range check, at the
# subscript and at the explicit dunder call alike (the subscript, proven in
# range, takes the unchecked twin as an int32 counter's does).


def main() -> None:
    xs = [1, 2, 3]
    i = 0
    while i < len(xs):
        print("sub", xs[i])  # tpyc: ok
        print("call", xs.__getitem__(i))  # tpyc: ok
        i += 1


main()
