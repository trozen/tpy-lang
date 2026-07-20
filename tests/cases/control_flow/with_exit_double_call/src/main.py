# Regression: a suppressing __exit__ must be called exactly once when the body
# raises and the catch suppresses. The normal-path __exit__ copy sits after the
# catches (so a throwing __exit__ isn't re-run by the with's own catch-all --
# see with_exit_raises_once), which puts it on the suppressing catch's
# fall-out path too; the suppressing arm jumps past it. We count visible "exit"
# prints in the output -- a double-call regression would emit two of them
# instead of one per `with`.


class Suppressor:
    def __enter__(self) -> int:
        return 1

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        if exc_val is not None:
            print(f"exit suppress: {str(exc_val)}")
            return True
        print("exit normal")
        return False


def fall_through(do_raise: bool) -> None:
    print(f"-- do_raise={do_raise} --")
    with Suppressor():
        if do_raise:
            raise ValueError("boom")
        print("body fall-through")
    print("after with")


def main() -> None:
    fall_through(False)
    fall_through(True)


main()
