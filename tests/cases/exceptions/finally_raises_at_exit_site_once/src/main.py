# Regression: a finally body that RAISES must run exactly once when control
# leaves the try via return / break / continue, and the FIRST exception must
# propagate. The exit-site copy sets its frame's guard before running, so the
# frame's own catch skips its copy rather than running the finally a second
# time; an enclosing frame's guard is still unset, so its finally still runs.

_code = 0


def bump() -> int:
    global _code
    _code += 1
    print(f"side-effect {_code}")
    return _code


class Err(Exception):
    def __init__(self, code: int) -> None:
        self.code = code


def ret_out(n: int) -> int:
    try:
        return n
    finally:
        raise Err(bump())


def brk_out() -> None:
    for i in range(3):
        try:
            if i == 1:
                break
            print(f"iter {i}")
        finally:
            if i == 1:
                raise Err(bump())


def cont_out() -> None:
    for i in range(2):
        try:
            continue
        finally:
            raise Err(bump())


def nested_ret() -> int:
    # The inner finally raises on the return path; the outer finally must
    # still run (its guard is unset), exactly once, and the inner exception
    # is the one that propagates.
    try:
        try:
            return 3
        finally:
            print("inner fin")
            raise Err(bump())
    finally:
        print("outer fin")


def main() -> None:
    global _code
    _code = 0
    print("-- ret_out --")
    try:
        ret_out(5)
    except Err as e:
        print(f"caught code={e.code}")

    _code = 0
    print("-- brk_out --")
    try:
        brk_out()
    except Err as e:
        print(f"caught code={e.code}")

    _code = 0
    print("-- cont_out --")
    try:
        cont_out()
    except Err as e:
        print(f"caught code={e.code}")

    _code = 0
    print("-- nested_ret --")
    try:
        nested_ret()
    except Err as e:
        print(f"caught code={e.code}")


main()
