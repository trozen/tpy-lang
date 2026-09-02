from tpy import Int32, nocopy
@nocopy
class H:
    _addr: tuple[str, Int32]
    def __init__(self, addr: tuple[str, Int32]) -> None:
        self._addr = addr
    def describe(self) -> str:
        p = self._addr
        return f'a {p}'
def main() -> None:
    print(H(('host', 80)).describe())
main()
