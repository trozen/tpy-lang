# try/finally (no-handler) shapes: hoisted first-declares read after the
# block, return through the finally (value + bare), a returning finally
# overriding the try's return, break/continue re-emitting the finally
# inside a loop, nested try/finally, and a try inside a with body (mixed
# finally-frame kinds on the return path).


class CM:
    n: int

    def __init__(self, n: int) -> None:
        self.n = n

    def __enter__(self) -> int:
        print("enter", self.n)
        return self.n

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        print("exit", self.n)


def hoisted() -> None:
    try:
        y = 5
        s = "in-try"
        print("try", y, s)
    finally:
        t = "in-finally"
        print("finally", t)
    print("after", y, s, t)


def ret_through(n: int) -> int:
    try:
        if n > 2:
            return n * 2
        print("no-return", n)
    finally:
        print("cleanup", n)
    return 0


def bare_ret(n: int) -> None:
    try:
        if n > 0:
            return
        print("fell", n)
    finally:
        print("bare-cleanup", n)


def override(n: int) -> int:
    try:
        if n > 0:
            return n * 10
        print("neg", n)
    finally:
        return -1


def loop_exits(k: int) -> int:
    total = 0
    for i in range(k):
        try:
            if i == 1:
                continue
            if i == 3:
                break
            total = total + i
        finally:
            total = total + 100
    return total


def nested() -> None:
    try:
        try:
            print("inner")
        finally:
            print("inner-finally")
        print("between")
    finally:
        print("outer-finally")


def mixed(n: int) -> int:
    with CM(n) as x:
        try:
            if x > 3:
                return x
            print("small", x)
        finally:
            print("try-cleanup", x)
    return -5


def term_body() -> int:
    try:
        return 4
    finally:
        print("term-cleanup")


def main() -> None:
    hoisted()
    print(ret_through(5))
    print(ret_through(1))
    bare_ret(1)
    bare_ret(-1)
    print(override(2))
    print(override(-2))
    print(loop_exits(6))
    nested()
    print(mixed(5))
    print(mixed(1))
    print(term_body())


main()
