# GeneratorExit is a normal raisable/catchable builtin. It inherits
# BaseException directly (not Exception), so `except Exception` must not
# swallow it.
def boom() -> None:
    raise GeneratorExit("closing")


def main() -> None:
    try:
        boom()
    except GeneratorExit as e:
        print("caught:", e)

    try:
        try:
            boom()
        except Exception:
            print("wrong: Exception caught GeneratorExit")
    except BaseException:
        print("BaseException caught it")


main()
