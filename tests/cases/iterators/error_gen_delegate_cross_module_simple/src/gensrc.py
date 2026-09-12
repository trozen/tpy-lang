from typing import Iterator
from tpy import int32


# SIMPLE (one yield in one loop): emitted as an `inline auto` factory
# returning a lambda wrapper, so it has no struct name to embed.
def walk() -> Iterator[int32]:
    for i in range(3):
        yield i
