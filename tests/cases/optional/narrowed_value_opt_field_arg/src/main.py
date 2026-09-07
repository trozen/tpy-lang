# A value-repr Optional FIELD at an Optional parameter slot: passed whole it is
# the field read, but a NARROWED read is the deref instead -- both have to reach
# the callee with the value the field holds.
import dialer
from tpy import Int32


class Conn:
    host: str
    timeout: float | None

    def __init__(self, h: str, t: float | None) -> None:
        self.host = h
        self.timeout = t

    def whole(self) -> Int32:
        return dialer.dial(self.host, self.timeout)

    def narrowed(self) -> Int32:
        if self.timeout is None:
            return 0
        return dialer.dial(self.host, self.timeout)


def main() -> None:
    print(Conn("h", None).whole(), Conn("h", 1.0).whole())
    print(Conn("h", None).narrowed(), Conn("h", 1.0).narrowed())


main()
