# A name first bound in two sibling `match` arms is one local with one type:
# an int in one arm and a float in the other is refused, as for if/else arms.


def pick(k: int) -> None:
    match k:
        case 1:
            y = 3
        case _:
            y = 2.5  # tpyc: error(/'y' is bound to int at line 8 and to float here/)
    print(y)


pick(1)
