# poll_ready[None](None) -- explicit-type-arg form of the parametrized
# poll_ready factory with T=None. Companion to the existing
# poll_ready_none() shim (which is a separate factory specialized
# pre-fix for the void-payload case); this exercises the generic-T=None
# path through the `Own[T]` parameter slot.
from tpy.coro import Poll, poll_ready
from tpy import Own


def make_ready() -> Own[Poll[None]]:
    return poll_ready[None](None)


def main() -> None:
    p = make_ready()
    print("ok")


main()
