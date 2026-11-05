#pragma once
#include <tpy/tpy.hpp>
#include <iostream>
#include <string>
#include <utility>

namespace probe {

// A native iterable whose __iter__ is observable.
struct Cursor : tpy::next_iter_mixin<Cursor, int32_t> {
    int32_t pos = 0;
    Cursor& __iter__() { return *this; }
    std::expected<int32_t, tpy::StopIteration> __next__() {
        if (pos == 2) return tpy::make_unexpected(tpy::StopIteration{});
        return ++pos;
    }
};
struct Source {
    Cursor __iter__() const {
        std::cout << "iter\n";
        return Cursor{};
    }
};

} // namespace probe
