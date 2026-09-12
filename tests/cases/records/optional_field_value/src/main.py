from tpy import int32


class Config:
    name: str
    max_retries: int32 | None

    def __init__(self, name: str):
        self.name = name
        self.max_retries = None

    def get_retries(self) -> int32 | None:
        return self.max_retries


# Basic: init to None, set, read
c = Config("test")
print(c.max_retries is None)
c.max_retries = 5
print(c.max_retries)
print(c.name)

# Return optional value-type field from method
r = c.get_retries()
print(r)
print(r is not None)

# Field-to-field value-type optional
c2 = Config("other")
c2.max_retries = c.max_retries
print(c2.max_retries)
