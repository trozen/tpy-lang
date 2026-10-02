# print(..., file=user_writable) wraps the target in a streambuf adapter
# (WritableOstream) that routes operator<< through write() / flush().
from tpy import int32

class Sink:
    parts: list[str]

    def __init__(self) -> None:
        self.parts = []

    def write(self, text: str) -> int32:
        self.parts.append(text)
        return int32(len(text))

    def flush(self) -> None:
        pass


class Closed:
    def write(self, text: str) -> int32:
        raise ValueError("closed")

    def flush(self) -> None:
        pass


class NoFlush:
    def write(self, text: str) -> int32:
        return len(text)

    def flush(self) -> None:
        raise ValueError("no flush")


# An exception raised by the sink's write() or flush() propagates out of the
# print, as in CPython (the adapter does not fold it into the stream state).
def raising_sink() -> None:
    c = Closed()
    try:
        print("x", file=c)  # tpyc: ok
        print("raise: not reached")
    except ValueError as e:
        print("raise: write " + str(e))
    n = NoFlush()
    try:
        print("y", file=n, flush=True)  # tpyc: ok -- the flush() raises
        print("raise: not reached")
    except ValueError as e:
        print("raise: flush " + str(e))


def main() -> None:
    raising_sink()
    s = Sink()
    print("hello", "world", 1, file=s)
    print("sep test", "x", file=s, sep="::", end="$\n")
    # Combined: file= sink with runtime sep / end (exercises the
    # WritableOstream adapter alongside the runtime-token codegen path).
    sep = "|"
    end = "?\n"
    print("a", "b", "c", file=s, sep=sep, end=end)
    # No-args print to the adapter -- creates and immediately destroys a
    # WritableOstream that emits just the end token.
    print(file=s)
    # Avoid repr() on strings -- newline-escape repr is currently buggy.
    print("captured", len(s.parts), "pieces:")
    for p in s.parts:
        print("  len=", len(p), sep="")

main()
