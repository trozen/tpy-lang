# Regression: an __exit__ that RAISES must run exactly once when control
# leaves the with body via return / break. Sibling of with_exit_raises_once
# (which covers the fall-through path): there the copy is moved outside the
# try, here the inline exit-site copy is guarded instead, since a `with` can
# be left from arbitrarily many points inside its body.


class Thrower:
    def __enter__(self) -> int:
        return 1

    def __exit__(self, et, ev, tb) -> None:
        print("exit ran")
        raise RuntimeError("from exit")


class Suppressor:
    def __enter__(self) -> int:
        return 1

    def __exit__(self, et, ev, tb) -> bool:
        print("exit suppress")
        return True


def ret_out() -> int:
    with Thrower():
        return 7


def brk_out() -> None:
    for i in range(3):
        with Thrower():
            if i == 1:
                break
            print(f"iter {i}")


def cont_out() -> None:
    for i in range(3):
        with Thrower():
            if i == 1:
                continue
            print(f"iter {i}")


def ret_out_suppressing() -> int:
    # A suppressing manager must not be handed its own successful exit as an
    # exception to suppress: the return still happens. The trailing return is
    # unreachable but required -- a suppressing __exit__ means the with body
    # is not proven to terminate (the bool could be True).
    with Suppressor():
        return 9
    return 0


def main() -> None:
    print("-- ret_out --")
    try:
        ret_out()
    except RuntimeError as e:
        print(f"caught: {str(e)}")

    print("-- brk_out --")
    try:
        brk_out()
    except RuntimeError as e:
        print(f"caught: {str(e)}")

    print("-- cont_out --")
    try:
        cont_out()
    except RuntimeError as e:
        print(f"caught: {str(e)}")

    print("-- ret_out_suppressing --")
    print(ret_out_suppressing())


main()
