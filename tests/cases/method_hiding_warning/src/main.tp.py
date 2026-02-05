class Animal:
    def speak(self) -> None:
        print("...")

    def make_noise(self) -> None:
        self.speak()

class Dog(Animal):
    def speak(self) -> None:
        print("Woof!")

d = Dog()
d.make_noise()
