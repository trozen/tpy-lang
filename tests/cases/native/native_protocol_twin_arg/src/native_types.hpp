#pragma once
#include <cstdint>

// A user binding over any iterable of int32: it reads the caller's object.
namespace nt {
template<class Xs> int32_t total(const Xs& xs) {
    int32_t n = 0;
    for (auto v : xs) n += v;
    return n;
}
}
