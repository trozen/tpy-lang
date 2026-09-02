from tpy import Int32, Int64

class Box:
    val: Int32
    def __init__(self, v: Int32) -> None:
        self.val = v

async def get(b: Box, n: Int32) -> Box | None:
    if n > 0:
        return b
    return None

async def f(b: Box, n: Int32) -> Int32:
    t = await get(b, n)
    if t is not None:
        return t.val
    return -1

def main() -> None:
    pass
main()
