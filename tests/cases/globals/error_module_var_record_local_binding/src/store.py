# Companion module: `main` drives this record global as `store.env`.
from typing import Iterator
from tpy import nocopy


@nocopy
class Env:
    data: dict[str, str]

    def __init__(self) -> None:
        self.data = {}

    def __getitem__(self, key: str) -> str:
        return self.data[key]

    def __setitem__(self, key: str, value: str) -> None:
        self.data[key] = value

    def __delitem__(self, key: str) -> None:
        del self.data[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self.data)


env: Env = Env()
