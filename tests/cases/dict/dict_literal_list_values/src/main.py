# A dict literal whose values are peer pending containers must unify them
# rather than reject as "mixed value types".
def main() -> None:
    d = {1: [1, 2], 2: [3, 4]}  # tpyc: ok
    print(d)
    d[1][0] = 9  # change is visible -> the stored value is a real container, not a copy
    print(d)

    # Peer pending-DICT values exercise the kernel's dict branch.
    dd = {1: {10: 1}, 2: {20: 2}}  # tpyc: ok
    print(dd)

main()
