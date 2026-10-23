# Whether a `while` body runs at least once is decided after the head's own
# evaluation: a call in the head may rebind what the later test reads, so
# the body's binding may not be assigned after the loop.


def f() -> None:
    i = 0

    def stop() -> bool:
        nonlocal i
        i = 10
        return False

    while stop() or i < 3:
        x = 42
    # The subject: `x` is bound only if the body ran.
    print(x)  # tpyc: error(/variable 'x' may not be assigned at this point/)


f()
