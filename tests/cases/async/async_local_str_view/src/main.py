# Str locals hoisted into a resumable frame exercise the view-vs-owned
# deferred resolution (PendingViewType): `view` stays a StrView, `owned` is
# promoted to an owned str by the augmented assign. Defined as an async METHOD
# so the method-body hoist path (the one the collection reorder fixed) is
# exercised for the view sink; both resolved forms must reach the frame fields.
import asyncio


class Greeter:
    async def run(self) -> None:
        view = "hello"
        owned = "wor"
        owned += "ld"
        await asyncio.sleep(0)
        print(view)
        print(owned)


def main() -> None:
    asyncio.run(Greeter().run())


main()
