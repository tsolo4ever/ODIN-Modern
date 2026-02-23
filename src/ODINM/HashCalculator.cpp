/******************************************************************************

    OdinM - Multi-Drive Clone Tool
    HashCalculator.cpp - SHA hash calculation implementation

******************************************************************************/

#include "stdafx.h"
#include "HashCalculator.h"

// ---------------------------------------------------------------------------
// Public: calculate SHA-1
// ---------------------------------------------------------------------------
bool CHashCalculator::CalculateSHA1(HANDLE hFile, ULONGLONG offset, ULONGLONG size,
    std::wstring& result, std::function<void(int)> progressCallback)
{
    return CalculateHash(CALG_SHA1, hFile, offset, size, result, progressCallback);
}

// ---------------------------------------------------------------------------
// Public: calculate SHA-256
// ---------------------------------------------------------------------------
bool CHashCalculator::CalculateSHA256(HANDLE hFile, ULONGLONG offset, ULONGLONG size,
    std::wstring& result, std::function<void(int)> progressCallback)
{
    return CalculateHash(CALG_SHA_256, hFile, offset, size, result, progressCallback);
}

// ---------------------------------------------------------------------------
// Public: calculate both hashes in one pass
// ---------------------------------------------------------------------------
bool CHashCalculator::CalculateBothHashes(HANDLE hFile, ULONGLONG offset, ULONGLONG size,
    std::wstring& sha1Result, std::wstring& sha256Result,
    std::function<void(int)> progressCallback)
{
    HCRYPTPROV hProv = 0;
    HCRYPTHASH hSHA1 = 0;
    HCRYPTHASH hSHA256 = 0;

    if (!CryptAcquireContext(&hProv, NULL, NULL, PROV_RSA_AES, CRYPT_VERIFYCONTEXT))
        return false;

    bool success = false;

    do {
        if (!CryptCreateHash(hProv, CALG_SHA1, 0, 0, &hSHA1)) break;
        if (!CryptCreateHash(hProv, CALG_SHA_256, 0, 0, &hSHA256)) break;

        // Seek to offset
        LARGE_INTEGER li;
        li.QuadPart = (LONGLONG)offset;
        if (!SetFilePointerEx(hFile, li, NULL, FILE_BEGIN)) break;

        const DWORD BUFFER_SIZE = 1024 * 1024; // 1 MB
        std::vector<BYTE> buffer(BUFFER_SIZE);
        ULONGLONG remaining = size;
        ULONGLONG processed = 0;

        success = true;
        while (remaining > 0 && success) {
            DWORD toRead = (DWORD)min((ULONGLONG)BUFFER_SIZE, remaining);
            DWORD bytesRead = 0;

            if (!ReadFile(hFile, buffer.data(), toRead, &bytesRead, NULL) || bytesRead == 0) {
                success = false;
                break;
            }

            CryptHashData(hSHA1,   buffer.data(), bytesRead, 0);
            CryptHashData(hSHA256, buffer.data(), bytesRead, 0);

            processed  += bytesRead;
            remaining  -= bytesRead;

            if (progressCallback && size > 0) {
                int pct = (int)((processed * 100) / size);
                progressCallback(pct);
            }
        }

        if (success) {
            // Retrieve SHA-1
            DWORD hashLen = 0, hashLenSize = sizeof(DWORD);
            CryptGetHashParam(hSHA1, HP_HASHSIZE, (BYTE*)&hashLen, &hashLenSize, 0);
            std::vector<BYTE> hashVal(hashLen);
            if (CryptGetHashParam(hSHA1, HP_HASHVAL, hashVal.data(), &hashLen, 0))
                sha1Result = BytesToHex(hashVal.data(), hashLen);
            else
                success = false;

            // Retrieve SHA-256
            hashLen = 0; hashLenSize = sizeof(DWORD);
            CryptGetHashParam(hSHA256, HP_HASHSIZE, (BYTE*)&hashLen, &hashLenSize, 0);
            hashVal.resize(hashLen);
            if (CryptGetHashParam(hSHA256, HP_HASHVAL, hashVal.data(), &hashLen, 0))
                sha256Result = BytesToHex(hashVal.data(), hashLen);
            else
                success = false;
        }
    } while (false);

    if (hSHA1)   CryptDestroyHash(hSHA1);
    if (hSHA256) CryptDestroyHash(hSHA256);
    CryptReleaseContext(hProv, 0);

    return success;
}

// ---------------------------------------------------------------------------
// Public convenience: SHA-1 of entire file by path
// ---------------------------------------------------------------------------
bool CHashCalculator::CalculateSHA1FromFile(const std::wstring& filePath,
    std::wstring& result, std::function<void(int)> progressCallback)
{
    HANDLE hFile = CreateFileW(filePath.c_str(), GENERIC_READ, FILE_SHARE_READ,
        NULL, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL);
    if (hFile == INVALID_HANDLE_VALUE)
        return false;

    LARGE_INTEGER fileSize = {};
    bool ok = GetFileSizeEx(hFile, &fileSize) &&
              CalculateHash(CALG_SHA1, hFile, 0, (ULONGLONG)fileSize.QuadPart,
                            result, progressCallback);
    CloseHandle(hFile);
    return ok;
}

// ---------------------------------------------------------------------------
// Public convenience: SHA-256 of entire file by path
// ---------------------------------------------------------------------------
bool CHashCalculator::CalculateSHA256FromFile(const std::wstring& filePath,
    std::wstring& result, std::function<void(int)> progressCallback)
{
    HANDLE hFile = CreateFileW(filePath.c_str(), GENERIC_READ, FILE_SHARE_READ,
        NULL, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL);
    if (hFile == INVALID_HANDLE_VALUE)
        return false;

    LARGE_INTEGER fileSize = {};
    bool ok = GetFileSizeEx(hFile, &fileSize) &&
              CalculateHash(CALG_SHA_256, hFile, 0, (ULONGLONG)fileSize.QuadPart,
                            result, progressCallback);
    CloseHandle(hFile);
    return ok;
}

// ---------------------------------------------------------------------------
// Private: core hash calculation
// ---------------------------------------------------------------------------
bool CHashCalculator::CalculateHash(ALG_ID algId, HANDLE hFile, ULONGLONG offset,
    ULONGLONG size, std::wstring& result, std::function<void(int)> progressCallback)
{
    HCRYPTPROV hProv = 0;
    HCRYPTHASH hHash = 0;

    if (!CryptAcquireContext(&hProv, NULL, NULL, PROV_RSA_AES, CRYPT_VERIFYCONTEXT))
        return false;

    bool success = false;

    do {
        if (!CryptCreateHash(hProv, algId, 0, 0, &hHash)) break;

        LARGE_INTEGER li;
        li.QuadPart = (LONGLONG)offset;
        if (!SetFilePointerEx(hFile, li, NULL, FILE_BEGIN)) break;

        const DWORD BUFFER_SIZE = 1024 * 1024; // 1 MB
        std::vector<BYTE> buffer(BUFFER_SIZE);
        ULONGLONG remaining = size;
        ULONGLONG processed = 0;

        success = true;
        while (remaining > 0 && success) {
            DWORD toRead = (DWORD)min((ULONGLONG)BUFFER_SIZE, remaining);
            DWORD bytesRead = 0;

            if (!ReadFile(hFile, buffer.data(), toRead, &bytesRead, NULL) || bytesRead == 0) {
                success = false;
                break;
            }

            if (!CryptHashData(hHash, buffer.data(), bytesRead, 0)) {
                success = false;
                break;
            }

            processed += bytesRead;
            remaining -= bytesRead;

            if (progressCallback && size > 0) {
                int pct = (int)((processed * 100) / size);
                progressCallback(pct);
            }
        }

        if (success) {
            DWORD hashLen = 0, hashLenSize = sizeof(DWORD);
            CryptGetHashParam(hHash, HP_HASHSIZE, (BYTE*)&hashLen, &hashLenSize, 0);
            std::vector<BYTE> hashVal(hashLen);
            if (CryptGetHashParam(hHash, HP_HASHVAL, hashVal.data(), &hashLen, 0))
                result = BytesToHex(hashVal.data(), hashLen);
            else
                success = false;
        }
    } while (false);

    if (hHash) CryptDestroyHash(hHash);
    CryptReleaseContext(hProv, 0);

    return success;
}

// ---------------------------------------------------------------------------
// Private: convert raw bytes to uppercase hex string
// ---------------------------------------------------------------------------
std::wstring CHashCalculator::BytesToHex(const BYTE* data, DWORD length)
{
    std::wstring result;
    result.reserve(length * 2);

    for (DWORD i = 0; i < length; i++) {
        wchar_t hex[3];
        swprintf_s(hex, L"%02X", data[i]);
        result += hex;
    }

    return result;
}
