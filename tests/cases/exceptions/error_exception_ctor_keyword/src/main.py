# A builtin exception's constructor takes no keyword arguments, as in CPython
# (TypeError: OSError() takes no keyword arguments): its parameters are
# positional-only, at a constructor overload group too.


def main() -> None:
    # OSError's group would rank `(errno, strerror)` the winner by position.
    e = OSError(2, strerror="x")  # tpyc: error(/parameter 'strerror' is positional-only and cannot be passed by keyword/)
    print(e.errno)


main()
