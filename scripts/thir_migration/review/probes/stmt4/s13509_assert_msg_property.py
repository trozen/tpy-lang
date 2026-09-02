from tpy import Int32
class F:
    _m: str
    def __init__(self, m: str):
        self._m = m
    @property
    def msg(self) -> str:
        return self._m
def rejected(n: Int32, f: F) -> Int32:
    assert n > 0, f.msg
    return n
def main() -> None:
    print(rejected(1, F('x')))
main()
