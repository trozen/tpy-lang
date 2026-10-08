# An uncaught SystemExit with an int code exits with that status after
# unwinding and reports nothing on stderr (no panic report).
import sys
from tpy import int32


class Shutdown:
    def stop(self, code: int32) -> None:
        try:
            # The subject: the status is 3 and stderr stays empty.
            sys.exit(code)
        finally:
            print("stop: finally")


def main() -> None:
    Shutdown().stop(3)
    print("main: not reached")


main()
