# @unsafe_sync and @nosync on the same function are contradictory
# (the function-decorator arm, parallel to the class case).
from tpy import int32, unsafe_sync, nosync
from typing import Iterator

@unsafe_sync
@nosync
def gen(n: int32) -> Iterator[int32]:  # tpyc: error(/contradictory Sync-override decorators/)
    yield n
