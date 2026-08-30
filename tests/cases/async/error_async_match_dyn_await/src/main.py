# A `match` on a @dynamic protocol subject dispatches through a dynamic_cast
# chain, not the resumable frame's arm routing, so an `await` in an arm body is
# rejected at the match rather than silently dropped from the state machine.
import asyncio
from typing import Protocol

from tpy import dynamic


@dynamic
class Pet(Protocol):
    def name(self) -> str: ...


class Dog(Pet):
    def name(self) -> str:
        return "dog"


async def step() -> None:
    pass


# The diagnostic is reported at the match, but the `await` two lines down is
# what makes the arm unemittable.
async def describe(p: Pet) -> str:
    match p:  # tpyc: error(/`await` inside a `match` on a @dynamic/)
        case Dog():
            await step()
            return "dog"
        case _:
            return "other"


def main() -> None:
    print(asyncio.run(describe(Dog())))


main()
