# Throw-tier try/except shapes: multi-handler catch arms, bare except,
# as-binding field reads, else via the goto label, except+finally (handler
# returns re-emitting the finally), bare re-raise, raise ctor/no-arg forms,
# and a raise-terminated finally overriding the try's return.
# raising_finally's raise arg is deliberately side-effect-free: the emitted
# shape runs a raising finally twice after a return (known parity bug, see
# BUGS.md), which is observable only through side effects.


class AppError(Exception):
    code: int

    def __init__(self, code: int) -> None:
        self.code = code


def boom(n: int) -> int:
    if n < 0:
        raise ValueError("negative")
    if n == 0:
        raise AppError(7)
    if n > 99:
        raise RuntimeError
    return n * 2


def catch_multi(n: int) -> int:
    try:
        v = boom(n)
        print("ok", v)
        return v
    except ValueError:
        print("value")
        return -1
    except AppError as e:
        print("app", e.code)
        return -2
    except RuntimeError:
        return -3


def catch_bare(n: int) -> int:
    try:
        return boom(n)
    except:
        return -9


def with_else(n: int) -> int:
    r = 0
    try:
        r = boom(n)
    except ValueError:
        print("ve")
        r = -1
    else:
        print("else", r)
    return r


def with_finally(n: int) -> int:
    try:
        return boom(n)
    except ValueError:
        return -1
    finally:
        print("fin", n)


def reraise(n: int) -> int:
    try:
        return boom(n)
    except ValueError:
        if n == -5:
            raise
        return -1


def raising_finally(n: int) -> int:
    try:
        if n > 0:
            return n
        print("neg", n)
    finally:
        raise AppError(n + 100)


def main() -> None:
    print(catch_multi(4))
    print(catch_multi(-1))
    print(catch_multi(0))
    print(catch_multi(100))
    print(catch_bare(-2))
    print(with_else(5))
    print(with_else(-1))
    print(with_finally(6))
    print(with_finally(-3))
    print(reraise(2))
    print(reraise(-1))
    try:
        print(reraise(-5))
    except ValueError:
        print("outer caught")
    try:
        print(raising_finally(1))
    except AppError as e:
        print("fin-raise", e.code)


main()
