# Regression guard: get_running_loop() called with no loop running raises
# RuntimeError (CPython parity). Exercised here from a plain sync context.
import asyncio


def main() -> None:
    try:
        asyncio.get_running_loop()
        print("no error")
    except RuntimeError:
        print("RuntimeError")


main()
