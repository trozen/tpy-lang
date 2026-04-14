# bytes param assigned to bytes field: codegen must construct
# std::vector<uint8_t> from std::span<const uint8_t> in the member init list.
class Packet:
    def __init__(self, data: bytes) -> None:
        self.data = data

class MultiField:
    def __init__(self, name: str, payload: bytes) -> None:
        self.name = name
        self.payload = payload

def main() -> None:
    p = Packet(b"hello")
    print(p.data)

    m = MultiField("test", b"\x01\x02\x03")
    print(m.name)
    print(m.payload)

main()
