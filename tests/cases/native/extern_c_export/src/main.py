from tpy.extern import extern_c
from tpy import Int32

@extern_c
def app_init() -> None:
    print("app_init called")

@extern_c("app_tick")
def game_tick(time: Int32) -> None:
    print(time)
