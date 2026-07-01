# An exception raised inside the task's run() is re-raised by join() in the
# joining thread, catchable by ordinary try/except (std::future cross-thread
# result-or-rethrow). Consuming via join() on the exception path also clears the
# abort-on-drop check, so the handle drops cleanly afterwards.
from tpy.thread import spawn


class Boom:
    def run(self) -> int:
        raise ValueError("boom on the worker thread")


def main() -> None:
    h = spawn[int, Boom](Boom())
    try:
        print(h.join())
    except ValueError as e:
        print("caught:", e)


main()
