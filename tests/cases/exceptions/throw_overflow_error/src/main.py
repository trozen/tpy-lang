# Explicit `raise OverflowError(...)` is the throw-tier path; user code can
# catch it the same way as runtime-thrown OverflowError. Inherits from
# ArithmeticError so `except ArithmeticError` also catches it.


def fail() -> None:
    raise OverflowError("custom: value too large")


def main() -> None:
    try:
        fail()
    except OverflowError as e:
        print("caught OverflowError:", str(e))

    try:
        fail()
    except ArithmeticError as e:
        print("caught via ArithmeticError:", str(e))


main()
