# Regression: an async returning a short (SSO) str must survive moving through
# Poll<std::string> (the old UninitArrayStorage memcpy-move dangled it; ASan-only).
import asyncio


async def greet() -> str:
    await asyncio.sleep(0)
    return "hi"


def main() -> None:
    print(asyncio.run(greet()))
    bound = asyncio.run(greet())
    print(bound)


main()
