# A handler must take (signum, frame): one taking only the signal number is
# refused at the signal.signal call (the message is BUGS.md#misleading-not-a-variable).
import signal


def on_term(signum: int) -> None:
    print("term", signum)


def main() -> None:
    signal.signal(signal.SIGTERM, on_term)  # tpyc: error(/'on_term' is not a variable/)


main()
