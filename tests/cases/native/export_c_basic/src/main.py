# Test @export(binding="C") for C function exports
from tpy.extern import export
from tpy import int32

@export(binding="C")
def app_init() -> None:
    print("app_init called")

@export("app_tick", binding="C")
def game_tick(time: int32) -> None:
    print(time)

app_init()
game_tick(int32(42))
