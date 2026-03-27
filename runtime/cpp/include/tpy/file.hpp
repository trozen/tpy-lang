/**
 * TurboPython Runtime - File I/O
 *
 * TextFile: text-mode file wrapper for the open() builtin.
 * Supports read/write/append modes, readline, readlines,
 * and context manager protocol (__enter__/__exit__).
 */

#pragma once

#include "core.hpp"

#include <fstream>
#include <sstream>
#include <string>
#include <string_view>
#include <vector>

namespace tpy {

class TextFile {
    std::fstream fs_;
    std::string path_;
    bool readable_ = false;
    bool writable_ = false;
    bool closed_ = false;

public:
    TextFile(std::string_view path, std::string_view mode) : path_(path) {
        std::ios_base::openmode m{};
        if (mode == "r") {
            m = std::ios::in;
            readable_ = true;
        } else if (mode == "w") {
            m = std::ios::out | std::ios::trunc;
            writable_ = true;
        } else if (mode == "a") {
            m = std::ios::out | std::ios::app;
            writable_ = true;
        } else {
            tpy_panic(("open(): unsupported mode '" + std::string(mode) + "'").c_str());
        }
        fs_.open(path_, m);
        if (!fs_.is_open()) {
            tpy_panic(("open(): cannot open '" + std::string(path) + "'").c_str());
        }
    }

    TextFile(TextFile&&) = default;
    TextFile& operator=(TextFile&&) = default;
    TextFile(const TextFile&) = delete;
    TextFile& operator=(const TextFile&) = delete;

    std::string read() {
        if (!readable_) tpy_panic("read(): file not opened for reading");
        std::ostringstream ss;
        ss << fs_.rdbuf();
        return ss.str();
    }

    int32_t write(std::string_view text) {
        if (!writable_) tpy_panic("write(): file not opened for writing");
        fs_ << text;
        return static_cast<int32_t>(text.size());
    }

    std::string readline() {
        if (!readable_) tpy_panic("readline(): file not opened for reading");
        std::string line;
        if (!std::getline(fs_, line)) {
            return "";
        }
        // getline strips the newline delimiter; restore it unless we hit EOF
        // without a trailing newline (Python compat).
        if (!fs_.eof()) {
            line += '\n';
        }
        return line;
    }

    std::vector<std::string> readlines() {
        if (!readable_) tpy_panic("readlines(): file not opened for reading");
        std::vector<std::string> lines;
        std::string line;
        while (std::getline(fs_, line)) {
            if (!fs_.eof()) line += '\n';
            lines.push_back(std::move(line));
        }
        return lines;
    }

    void close() {
        if (!closed_) {
            fs_.close();
            closed_ = true;
        }
    }

    TextFile& __enter__() { return *this; }
    void __exit__() { close(); }

    friend std::ostream& operator<<(std::ostream& os, const TextFile& f) {
        return os << "<TextIO '" << f.path_ << "'>";
    }

    ~TextFile() { close(); }
};

inline TextFile builtin_open(std::string_view path) {
    return TextFile(path, "r");
}

inline TextFile builtin_open_mode(std::string_view path, std::string_view mode) {
    return TextFile(path, mode);
}

} // namespace tpy
