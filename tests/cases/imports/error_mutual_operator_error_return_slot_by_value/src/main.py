# A cycle member's `@error_return` operator may not return a tuple holding a
# peer's class: `std::expected` holds it by value (docs/LANGUAGE_FEATURES.md "Circular imports").
import a

print(a.run())
