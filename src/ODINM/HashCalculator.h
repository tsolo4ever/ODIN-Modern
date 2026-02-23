/******************************************************************************

    OdinM - Multi-Drive Clone Tool
    HashCalculator.h - SHA hash calculation using Windows CryptoAPI

******************************************************************************/

#pragma once
#include "stdafx.h"
#include <functional>

class CHashCalculator
{
public:
    // Calculate SHA-1 hash from an open file handle at given offset/size
    static bool CalculateSHA1(
        HANDLE hFile,
        ULONGLONG offset,
        ULONGLONG size,
        std::wstring& result,
        std::function<void(int)> progressCallback = nullptr
    );

    // Calculate SHA-256 hash from an open file handle at given offset/size
    static bool CalculateSHA256(
        HANDLE hFile,
        ULONGLONG offset,
        ULONGLONG size,
        std::wstring& result,
        std::function<void(int)> progressCallback = nullptr
    );

    // Calculate both SHA-1 and SHA-256 in a single read pass (efficient)
    static bool CalculateBothHashes(
        HANDLE hFile,
        ULONGLONG offset,
        ULONGLONG size,
        std::wstring& sha1Result,
        std::wstring& sha256Result,
        std::function<void(int)> progressCallback = nullptr
    );

    // Convenience: calculate SHA-1 of an entire file by path
    static bool CalculateSHA1FromFile(
        const std::wstring& filePath,
        std::wstring& result,
        std::function<void(int)> progressCallback = nullptr
    );

    // Convenience: calculate SHA-256 of an entire file by path
    static bool CalculateSHA256FromFile(
        const std::wstring& filePath,
        std::wstring& result,
        std::function<void(int)> progressCallback = nullptr
    );

private:
    static bool CalculateHash(
        ALG_ID algId,
        HANDLE hFile,
        ULONGLONG offset,
        ULONGLONG size,
        std::wstring& result,
        std::function<void(int)> progressCallback
    );

    static std::wstring BytesToHex(const BYTE* data, DWORD length);
};
