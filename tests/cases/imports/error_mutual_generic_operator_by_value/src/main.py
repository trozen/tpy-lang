# A cycle member's generic class operator is a header template, so it may not
# take a peer's value type by value (docs/LANGUAGE_FEATURES.md "Circular imports").
import a

print(a.run())
