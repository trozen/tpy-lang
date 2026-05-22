# bytes field assigned in `__init__` AFTER a side-effecting body
# statement: the MIL hoist must demote the assign to the body, and the
# body assign must include the span->vector conversion (parallel to the
# MIL-path conversion). Without it the emitted `this->data = data;`
# would be `std::vector<uint8_t> = std::span<const uint8_t>` and fail
# C++ compile.

class Holder:
    data: bytes
    tag: str

    def __init__(self, data: bytes, tag: str) -> None:
        print("constructing", tag)
        self.data = data
        self.tag = tag


def main() -> None:
    h = Holder(b"hello", "h")
    print(len(h.data), h.tag)


main()
