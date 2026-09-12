# Error: @auto_readonly is not valid on __init__.
from tpy import int32, auto_readonly

class Bad:
    x: int32

    @auto_readonly  # tpyc: error(/@auto_readonly is not valid on '__init__'/)
    def __init__(self) -> None:
        self.x = int32(0)

def main() -> None:
    pass
