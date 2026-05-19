# async @staticmethod is not supported -- staticmethods don't capture
# self, but the async-method codegen path assumes the receiver-capture
# convention. Rejected at parse.
import asyncio


class S:
    @staticmethod
    async def go() -> int:  # tpyc: error(/async @staticmethod is not yet supported/)
        return 1


def main() -> None:
    print("ok")


main()
