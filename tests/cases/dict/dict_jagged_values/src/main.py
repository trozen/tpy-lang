# A dict literal whose list values have different lengths: the peer values
# reconcile to list[int] rather than being rejected as mixed value types.
def main() -> None:
    d = {1: [1, 2], 2: [3, 4, 5]}  # tpyc: ok
    print(d)
    d[1].append(9)  # stored value is a real container -> change observed
    print(d)

main()
