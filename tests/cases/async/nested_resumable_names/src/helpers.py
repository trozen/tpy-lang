# Imported nested frames must use this defining namespace through a Python alias.
# tpy: cpp_namespace("nested_case::helpers")
class Library:
    class Worker:
        value: int

        def __init__(self, value: int) -> None:
            self.value = value

        async def compute(self, delta: int) -> int:  # tpyc: ok
            self.value += delta
            return self.value
