from tpy.extern import native, export
from tpy import Int32

@native(binding="C")
def puts(s: str) -> Int32: ...

@export(binding="C")
def greet(name: str) -> None:
    puts(name)
