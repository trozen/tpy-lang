# Same-name fields with the leading-underscore "private" idiom: the
# mechanism is identical to public fields; each subobject legitimately
# owns its own state, addressed via BaseN._field.
from tpy import int32


class RateLimiter:
    _count: int32


class CacheStats:
    _count: int32


class Service(RateLimiter, CacheStats):
    def __init__(self) -> None:
        RateLimiter._count = 0
        CacheStats._count = 0

    def tick_request(self) -> None:
        RateLimiter._count += 1  # aug-assign through the unbound-self form

    def tick_cache(self) -> None:
        CacheStats._count = CacheStats._count + 1

    def report(self) -> str:
        return "req=" + str(RateLimiter._count) + " cache=" + str(CacheStats._count)


def main() -> None:
    s = Service()
    s.tick_request()
    s.tick_request()
    s.tick_cache()
    print(s.report())


main()
