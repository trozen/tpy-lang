# Event is single-awaiter in v1 (matches Future): a second poll while
# a waker is already registered raises ValueError. Driven via
# `tpy.coro.poll_once` so the raise happens at the test level rather
# than inside a spawned task (where exceptions are swallowed in v1).
from asyncio import Event
from tpy.coro import poll_once


def main() -> None:
    e = Event()
    poll_once(e)
    try:
        poll_once(e)
        print("ERROR: second poll should have raised")
    except ValueError as ex:
        print("caught:", ex)


main()
