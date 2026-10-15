# A cycle member's class inheriting a generic class that stores its argument,
# over a peer's class, holds that class inside itself (docs/LANGUAGE_FEATURES.md "Circular imports").
import a

print(a.run())
