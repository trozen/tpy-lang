# @unsafe_sync and @nosync on the same function are contradictory
# (the function-decorator arm, parallel to the class case).
from tpy import Int32, unsafe_sync, nosync
from typing import Iterator

@unsafe_sync
@nosync
def gen(n: Int32) -> Iterator[Int32]:  # tpyc: error(/contradictory Sync-override decorators/)
    yield n
