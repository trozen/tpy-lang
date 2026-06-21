# An async frame holding a list-of-lists local across an await: the frame field
# carries a pending list element that must be finalized after resolution.
import asyncio
async def work() -> int:
    rows = [[i, i + 1] for i in range(3) if i > 0]
    await asyncio.sleep(0)
    rows[0][1] = 50
    return rows[0][0] + rows[0][1]
async def main() -> None:
    print(await work())
asyncio.run(main())
