# OSError's structured `.errno` / `.strerror` attributes: writable after
# construction, readable through an `except OSError` borrow, preserved across
# clone/re-raise, and -- the regression guard -- accessible on a USER-DEFINED
# subclass (the native_field C++ rename must apply through the TPy-level
# subclass, not just on OSError itself). str(e) is deliberately not printed:
# CPython reformats it to "[Errno N] strerror" once both attributes are set,
# where TPy keeps the constructed message (declared divergence).
from tpy import String


class DeviceError(OSError):
    def __init__(self, message: String = "") -> None:
        super().__init__(message)


def fail(direct: bool) -> None:
    if direct:
        e = OSError("io failed")
        e.errno = 5
        e.strerror = "Input/output error"
        raise e
    d = DeviceError("device gone")
    d.errno = 19
    d.strerror = "No such device"
    raise d


def main() -> None:
    try:
        fail(True)
    except OSError as e:
        print(e.errno, e.strerror)
    try:
        fail(False)
    except DeviceError as e:
        print(e.errno, e.strerror)
    # Subclass instance caught through the OSError base still carries them.
    try:
        fail(False)
    except OSError as e:
        print(e.errno, e.strerror)


main()
