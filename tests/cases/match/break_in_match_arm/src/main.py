# break/continue inside switch-lowered match arms must target the enclosing
# Python loop, not the C++ switch: plain loop, for/else loop, and the
# string-switch dispatch path.
from enum import Enum


class Color(Enum):
    Red = 1
    Green = 2
    Blue = 3


def break_plain() -> None:
    for c in [Color.Red, Color.Green, Color.Blue]:
        match c:
            case Color.Green:
                break
            case _:
                print("p", c.name)
    print("plain done")


def break_with_else() -> None:
    for c in [Color.Red, Color.Green, Color.Blue]:
        match c:
            case Color.Green:
                break
            case _:
                print("e", c.name)
    else:
        print("else ran")
    print("after else loop")


def continue_in_arm() -> None:
    for c in [Color.Red, Color.Green, Color.Blue]:
        match c:
            case Color.Green:
                continue
            case _:
                print("c", c.name)


def break_in_while() -> None:
    i = 0
    while i < 5:
        match i:
            case 3:
                break
            case _:
                print("w", i)
        i = i + 1
    print("while done")


def break_in_str_switch() -> None:
    for s in ["a", "bb", "stop", "ccc"]:
        match s:
            case "a":
                print("s a")
            case "bb":
                print("s bb")
            case "stop":
                break
            case "ccc":
                print("s ccc")
            case "dddd":
                print("s dddd")
            case _:
                print("s other")
    print("str done")


def main() -> None:
    break_plain()
    break_with_else()
    continue_in_arm()
    break_in_while()
    break_in_str_switch()


main()
