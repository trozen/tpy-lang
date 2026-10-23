# Deleting a slice is not supported yet (BUGS.md#del-slice-rejected): on a
# receiver that has a __delitem__ the rejection says so in plain words.


def main() -> None:
    xs = [1, 2, 3, 4]
    i = 2
    del xs[:i]  # tpyc: error(/deleting a slice \('del xs\[a:b\]'\) is not yet supported/)
    print(xs)


main()
