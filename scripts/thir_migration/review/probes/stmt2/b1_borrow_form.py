from tpy import int32, int64

class Box:
    val: int32
    def __init__(self, v: int32) -> None:
        self.val = v

async def get(b: Box, n: int32) -> Box | None:
    if n > 0:
        return b
    return None

async def f(b: Box, n: int32) -> int32:
    t = await get(b, n)
    if t is not None:
        return t.val
    return -1

def main() -> None:
    pass
main()
