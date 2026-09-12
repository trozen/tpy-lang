# @nocopy field that IS default-constructible still errors when assigned in a branch.
# The split-point check warns (default ctor exists), then walk_body errors on branch assign.
from tpy import nocopy, int32

@nocopy
class Token:
    id: int32
    def __init__(self):
        self.id = int32(0)

class Parser:
    tok: Token

    def __init__(self, flag: bool):
        if flag:
            self.tok = Token()  # tpyc: error(/move-only or has '__del__'/)
        else:
            self.tok = Token()  # same issue, not reported (first error stops collection)

def main() -> None:
    p = Parser(True)
    print(p.tok.id)

main()
