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


def main() -> None:
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
