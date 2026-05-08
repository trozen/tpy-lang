# Standalone `await sub()` -- result is discarded; sub runs for its
# side effects.
from tpy.coro import poll_once

async def side_effect() -> None:
    print("inside-side-effect")

async def caller() -> None:
    await side_effect()
    print("after-await")

def main() -> None:
    if poll_once(caller()).is_ready():
        print("done")

main()
