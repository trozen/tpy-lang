# async methods are not yet supported in v1. Rejected at parse time.
class Fetcher:
    url: str

    def __init__(self, url: str) -> None:
        self.url = url

    async def fetch(self) -> int:  # tpyc: error(/async methods .* not yet supported/)
        return 42


def main() -> None:
    print("ok")

main()
