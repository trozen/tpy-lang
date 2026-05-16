# Regression: a suppressing __exit__ must be called exactly once when the body
# raises and the catch suppresses. Earlier codegen emitted the normal-path
# __exit__ AFTER the catches, so a suppressing path would call __exit__ a
# second time with exc_val=None. The fix moves the normal-path call inside
# the try{}, only reachable on actual fall-through. We count visible "exit"
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
