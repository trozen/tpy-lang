# A bytes parameter held across a yield is captured owned in the generator
# frame (the generator sibling of async_bytes_param).
from typing import Iterator


def byte_vals(data: bytes) -> Iterator[int]:
    i = 0
    while i < len(data):
        yield data[i]
        i += 1


def main() -> None:
    total = 0
    for v in byte_vals(b"ABC"):
        total += v
    print(total)


main()
