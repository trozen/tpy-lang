# print(..., flush=True) appends `<< std::flush` to the chain.
# For user Writable targets this triggers the adapter's sync(), which
# calls W::flush(); we count flushes to verify.
from tpy import int32

class CountingSink:
    parts: list[str]
    flushes: int32

    def __init__(self) -> None:
        self.parts = []
        self.flushes = int32(0)

    def write(self, text: str) -> int32:
        self.parts.append(text)
        return int32(len(text))

    def flush(self) -> None:
        self.flushes += int32(1)


def main() -> None:
    print("flush via stdout", flush=True)
    print("no-flush via stdout")

    s = CountingSink()
    print("a", file=s)
    print("flushes after no-flush:", s.flushes)

    print("b", file=s, flush=True)
    print("flushes after flush=True:", s.flushes)

    print("c", file=s, flush=False)
    print("flushes after flush=False:", s.flushes)

main()
