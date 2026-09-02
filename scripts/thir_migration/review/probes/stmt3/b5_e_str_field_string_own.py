from tpy import Int32, String, Own
class S:
    t: String
    def __init__(self) -> None:
        self.t = String('a')
    def get(self) -> String:
        return self.t
def main() -> None:
    s = S()
    print(s.get())
main()
