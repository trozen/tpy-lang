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

struct FileFlags {
    std::ios_base::openmode mode;
    bool readable;
    bool writable;

    static const FileFlags text_read;
    static const FileFlags binary_read;

    static FileFlags parse_text(std::string_view mode) {
        if (mode == "r" || mode == "rt")
            return {std::ios::in, true, false};
        if (mode == "w" || mode == "wt")
            return {std::ios::out | std::ios::trunc, false, true};
        if (mode == "a" || mode == "at")
            return {std::ios::out | std::ios::app, false, true};
        if (mode == "r+" || mode == "r+t" || mode == "rt+")
            return {std::ios::in | std::ios::out, true, true};
        if (mode == "w+" || mode == "w+t" || mode == "wt+")
            return {std::ios::in | std::ios::out | std::ios::trunc, true, true};
        if (mode == "a+" || mode == "a+t" || mode == "at+")
            return {std::ios::in | std::ios::out | std::ios::app, true, true};
        if (mode == "x" || mode == "xt")
            return {std::ios::out | std::ios::trunc | std::ios::noreplace, false, true};
        if (mode == "x+" || mode == "x+t" || mode == "xt+")
            return {std::ios::in | std::ios::out | std::ios::trunc | std::ios::noreplace, true, true};
        raise<ValueError>("invalid mode: '{}'", mode);
    }

    static FileFlags parse_binary(std::string_view mode) {
        std::ios_base::openmode base = std::ios::binary;
        if (mode == "rb")
            return {base | std::ios::in, true, false};
        if (mode == "wb")
            return {base | std::ios::out | std::ios::trunc, false, true};
        if (mode == "ab")
            return {base | std::ios::out | std::ios::app, false, true};
        if (mode == "r+b" || mode == "rb+")
            return {base | std::ios::in | std::ios::out, true, true};
        if (mode == "w+b" || mode == "wb+")
            return {base | std::ios::in | std::ios::out | std::ios::trunc, true, true};
        if (mode == "a+b" || mode == "ab+")
            return {base | std::ios::in | std::ios::out | std::ios::app, true, true};
        if (mode == "xb")
            return {base | std::ios::out | std::ios::trunc | std::ios::noreplace, false, true};
        if (mode == "x+b" || mode == "xb+")
            return {base | std::ios::in | std::ios::out | std::ios::trunc | std::ios::noreplace, true, true};
        raise<ValueError>("invalid mode: '{}'", mode);
    }
};

inline const FileFlags FileFlags::text_read = {std::ios::in, true, false};
inline const FileFlags FileFlags::binary_read = {std::ios::binary | std::ios::in, true, false};

class TextFile {
    std::fstream fs_;
    std::string path_;
    FileFlags flags_{};
    bool closed_ = false;

public:
    TextFile(std::string_view path, FileFlags flags) : path_(path), flags_(flags) {
        fs_.open(path_, flags_.mode);
        if (!fs_.is_open()) {
            raise<::tpy::FileNotFoundError>("open(): cannot open '{}'", path);
        }
    }

    TextFile(TextFile&&) = default;
    TextFile& operator=(TextFile&&) = default;
    TextFile(const TextFile&) = delete;
    TextFile& operator=(const TextFile&) = delete;

    std::string read() {
        if (!flags_.readable) raise<OSError>("read(): file not opened for reading");
        std::ostringstream ss;
        ss << fs_.rdbuf();
        return ss.str();
    }

    int32_t write(std::string_view text) {
        if (!flags_.writable) raise<OSError>("write(): file not opened for writing");
        fs_ << text;
        return static_cast<int32_t>(text.size());
    }

    void flush() {
        if (!flags_.writable) raise<OSError>("flush(): file not opened for writing");
        fs_.flush();
    }

    std::ostream& sink() { return fs_; }

    std::string readline() {
        if (!flags_.readable) raise<OSError>("readline(): file not opened for reading");
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
        if (!flags_.readable) raise<OSError>("readlines(): file not opened for reading");
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

class BinaryFile {
    std::fstream fs_;
    std::string path_;
    FileFlags flags_{};
    bool closed_ = false;

public:
    BinaryFile(std::string_view path, FileFlags flags) : path_(path), flags_(flags) {
        fs_.open(path_, flags_.mode);
        if (!fs_.is_open()) {
            raise<::tpy::FileNotFoundError>("open(): cannot open '{}'", path);
        }
    }

    BinaryFile(BinaryFile&&) = default;
    BinaryFile& operator=(BinaryFile&&) = default;
    BinaryFile(const BinaryFile&) = delete;
    BinaryFile& operator=(const BinaryFile&) = delete;

    std::vector<uint8_t> read() {
        if (!flags_.readable) raise<OSError>("read(): file not opened for reading");
        return std::vector<uint8_t>(
            std::istreambuf_iterator<char>(fs_),
            std::istreambuf_iterator<char>()
        );
    }

    std::vector<uint8_t> readline() {
        if (!flags_.readable) raise<OSError>("readline(): file not opened for reading");
        std::string line;
        if (!std::getline(fs_, line)) {
            return {};
        }
        std::vector<uint8_t> result(line.begin(), line.end());
        if (!fs_.eof()) {
            result.push_back('\n');
        }
        return result;
    }

    std::vector<std::vector<uint8_t>> readlines() {
        if (!flags_.readable) raise<OSError>("readlines(): file not opened for reading");
        std::vector<std::vector<uint8_t>> lines;
        std::string line;
        while (std::getline(fs_, line)) {
            std::vector<uint8_t> row(line.begin(), line.end());
            if (!fs_.eof()) row.push_back('\n');
            lines.push_back(std::move(row));
        }
        return lines;
    }

    int32_t write(std::span<const uint8_t> data) {
        if (!flags_.writable) raise<OSError>("write(): file not opened for writing");
        fs_.write(reinterpret_cast<const char*>(data.data()),
                  static_cast<std::streamsize>(data.size()));
        return static_cast<int32_t>(data.size());
    }

    void close() {
        if (!closed_) {
            fs_.close();
            closed_ = true;
        }
    }

    BinaryFile& __enter__() { return *this; }
    void __exit__() { close(); }

    friend std::ostream& operator<<(std::ostream& os, const BinaryFile& f) {
        return os << "<BinaryIO '" << f.path_ << "'>";
    }

    ~BinaryFile() { close(); }
};

// --- Builtin open() helpers ---

inline TextFile builtin_open(std::string_view path) {
    return TextFile(path, FileFlags::text_read);
}

inline TextFile builtin_open_mode(std::string_view path, std::string_view mode) {
    return TextFile(path, FileFlags::parse_text(mode));
}

inline BinaryFile builtin_open_binary(std::string_view path, std::string_view mode = "rb") {
    return BinaryFile(path, FileFlags::parse_binary(mode));
}

} // namespace tpy
