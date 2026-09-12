from tpy import int32, nocopy
@nocopy
class H:
    _addr: tuple[str, int32]
    def __init__(self, addr: tuple[str, int32]) -> None:
        self._addr = addr
    def describe(self) -> str:
        p = self._addr
        return f'a {p}'
def main() -> None:
    print(H(('host', 80)).describe())
main()
