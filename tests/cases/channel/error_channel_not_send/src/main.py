# Channel enforces T: Send via the channel() factory bound -- a non-Send
# payload (raw-pointer field) is rejected with the why-not-send chain.
from tpy import Ptr, int32
from tpy.channel import channel


class Buffer:
    n: int32


class SharedCache:
    data: Ptr[Buffer]


def main() -> None:
    tx, rx = channel[SharedCache](4)  # tpyc: error(/does not satisfy bound 'Send'/)


main()
