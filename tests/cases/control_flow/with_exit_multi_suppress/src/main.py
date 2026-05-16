# Regression: nested context managers where the inner __exit__ suppresses.
# Earlier codegen propagated the same `body_terminates` flag to every layer
# (computed from the Python body). When the body always-raises AND the inner
# layer can_suppress, the outer layer's body (the inner try-catch) can still
# fall through -- but `body_terminates=True` was inherited, skipping the
# outer's normal-path __exit__. Each outer layer flips to False once an
# inner layer may suppress.
#
# The expected output records every enter/exit. The inner ValueError is
# suppressed by Inner.__exit__; control flows out of the inner try-catch
# into Outer's "normal" exit path. Both __exit__ calls fire exactly once.


class Outer:
    def __enter__(self) -> int:
        print("outer enter")
        return 10

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if exc_val is None:
            print("outer exit (normal)")
        else:
            print(f"outer exit (exc: {str(exc_val)})")


class Inner:
    def __enter__(self) -> int:
        print("inner enter")
        return 20

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        if exc_val is not None:
            print(f"inner suppressing: {str(exc_val)}")
            return True
        print("inner exit (normal)")
        return False


def run() -> None:
    with Outer() as a, Inner() as b:
        print(f"body a={a} b={b}")
        raise ValueError("inner-only")
    print("after with")


def main() -> None:
    run()


main()
