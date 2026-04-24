# Ambiguous field writes are rejected just like ambiguous reads: two bases
# contribute `count`, so `self.count = v` cannot pick one subobject
# unambiguously.
from tpy import Int32


class RateLimiter:
    count: Int32


class CacheStats:
    count: Int32


class Service(RateLimiter, CacheStats):
    def bump(self) -> None:
        self.count = self.count + 1  # tpyc: error(/Ambiguous field 'count' inherited from/)
