import pkg.a


def grow(i: pkg.a.Item) -> None:  # tpyc: warning(/'pkg.a' is read while 'pkg.a' is still being imported.*cannot access submodule 'a' of module 'pkg'/)
    i.v += 1
