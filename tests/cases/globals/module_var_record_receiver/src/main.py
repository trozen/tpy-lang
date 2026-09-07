# A @nocopy record module-global of ANOTHER module driven through its mapping
# dunders: the module-qualified name is the receiver, the iterable and the
# `del` target, and every write is seen by the next read (a copy would not
# compile, since Env is @nocopy).
import store


def main() -> None:
    store.env["a"] = "x"  # tpyc: ok -- the module-qualified global as a setitem receiver
    store.env["b"] = "y"
    n = 0
    for k in store.env:  # tpyc: ok -- and as a for-loop iterable
        n = n + len(k)
    print(store.env["a"], store.env["b"], n)
    del store.env["a"]  # tpyc: ok -- and as a del target
    left = 0
    for k in store.env:
        left = left + 1
    print(left)


main()
