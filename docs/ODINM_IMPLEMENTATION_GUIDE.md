# OdinM Implementation Guide

## Current Progress Summary

**Status:** Project structure created, ready for implementation

**Completed:**
- ✅ Specification updated to use SHA-1 and SHA-256 only (proprietary hashes removed)
- ✅ Created basic project structure in `src/ODINM/`
- ✅ Created DriveSlot class for managing individual drive operations
- ✅ Created main dialog header (OdinMDlg.h)
- ✅ Created resource definitions (resource.h)
- ✅ Created stdafx.h/cpp for precompiled headers

**Files Created:**
```
src/ODINM/
├── stdafx.h              ✅ Precompiled header definitions
├── stdafx.cpp            ✅ Precompiled header implementation
├── resource.h            ✅ Resource ID definitions (simplified for SHA only)
├── DriveSlot.h           ✅ Drive slot class header
├── DriveSlot.cpp         ✅ Drive slot implementation
└── OdinMDlg.h            ✅ Main dialog header
```

**Still Needed:**
```
src/ODINM/
├── OdinMDlg.cpp          ❌ Main dialog implementation
├── HashConfigDlg.h       ❌ Hash configuration dialog header
├── HashConfigDlg.cpp     ❌ Hash configuration dialog implementation
├── HashCalculator.h      ❌ SHA hash calculation utility
├── HashCalculator.cpp    ❌ SHA hash implementation
├── OdinM.cpp             ❌ Application entry point
├── OdinM.rc              ❌ Resource script file
└── res/
    └── OdinM.ico         ❌ Application icon
```

Plus:
- ❌ OdinM.vcxproj (Visual Studio project file)
- ❌ Update ODIN.sln to include OdinM project

---

## Implementation Steps

### Step 1: Create HashCalculator Utility

This class handles SHA-1 and SHA-256 calculation using Windows CryptoAPI.

**File: `src/ODINM/HashCalculator.h`**
```cpp
#pragma once
#include "stdafx.h"

class CHashCalculator {
public:
    // Calculate SHA-1 hash from file/partition
    static bool CalculateSHA1(
        HANDLE hFile,
        ULONGLONG offset,
        ULONGLONG size,
        std::wstring& result,
        std::function<void(int)> progressCallback = nullptr
    );
    
    // Calculate SHA-256 hash from file/partition
    static bool CalculateSHA256(
        HANDLE hFile,
        ULONGLONG offset,
        ULONGLONG size,
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
```

**File: `src/ODINM/HashCalculator.cpp`**
```cpp
#include "stdafx.h"
#include "HashCalculator.h"

bool CHashCalculator::CalculateSHA1(HANDLE hFile, ULONGLONG offset, ULONGLONG size,
    std::wstring& result, std::function<void(int)> progressCallback)
{
    return CalculateHash(CALG_SHA1, hFile, offset, size, result, progressCallback);
}

bool CHashCalculator::CalculateSHA256(HANDLE hFile, ULONGLONG offset, ULONGLONG size,
    std::wstring& result, std::function<void(int)> progressCallback)
{
    return CalculateHash(CALG_SHA_256, hFile, offset, size, result, progressCallback);
}

bool CHashCalculator::CalculateHash(ALG_ID algId, HANDLE hFile, ULONGLONG offset,
    ULONGLONG size, std::wstring& result, std::function<void(int)> progressCallback)
{
    HCRYPTPROV hProv = 0;
    HCRYPTHASH hHash = 0;
    
    // Acquire crypto context
    if (!CryptAcquireContext(&hProv, NULL, NULL, PROV_RSA_AES, CRYPT_VERIFYCONTEXT)) {
        return false;
    }
    
    // Create hash object
    if (!CryptCreateHash(hProv, algId, 0, 0, &hHash)) {
        CryptReleaseContext(hProv, 0);
        return false;
    }
    
    // Seek to offset
    LARGE_INTEGER li;
    li.QuadPart = offset;
    if (!SetFilePointerEx(hFile, li, NULL, FILE_BEGIN)) {
        CryptDestroyHash(hHash);
        CryptReleaseContext(hProv, 0);
        return false;
    }
    
    // Read and hash data
    const DWORD BUFFER_SIZE = 1024 * 1024; // 1MB buffer
    BYTE* buffer = new BYTE[BUFFER_SIZE];
    ULONGLONG remaining = size;
    ULONGLONG processed = 0;
    
    bool success = true;
    while (remaining > 0 && success) {
        DWORD toRead = (DWORD)min((ULONGLONG)BUFFER_SIZE, remaining);
        DWORD bytesRead = 0;
        
        if (!ReadFile(hFile, buffer, toRead, &bytesRead, NULL) || bytesRead == 0) {
            success = false;
            break;
        }
        
        if (!CryptHashData(hHash, buffer, bytesRead, 0)) {
            success = false;
            break;
        }
        
        processed += bytesRead;
        remaining -= bytesRead;
        
        // Report progress
        if (progressCallback && size > 0) {
            int progress = (int)((processed * 100) / size);
            progressCallback(progress);
        }
    }
    
    delete[] buffer;
    
    if (success) {
        // Get hash value
        DWORD hashLen = 0;
        DWORD hashLenSize = sizeof(DWORD);
        CryptGetHashParam(hHash, HP_HASHSIZE, (BYTE*)&hashLen, &hashLenSize, 0);
        
        BYTE* hashValue = new BYTE[hashLen];
        if (CryptGetHashParam(hHash, HP_HASHVAL, hashValue, &hashLen, 0)) {
            result = BytesToHex(hashValue, hashLen);
        } else {
            success = false;
        }
        delete[] hashValue;
    }
    
    CryptDestroyHash(hHash);
    CryptReleaseContext(hProv, 0);
    
    return success;
}

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
```

---

### Step 2: Create Hash Configuration Dialog

**File: `src/ODINM/HashConfigDlg.h`**
```cpp
#pragma once
#include "stdafx.h"
#include "resource.h"
#include "OdinMDlg.h"

class CHashConfigDlg : public CDialogImpl<CHashConfigDlg>
{
public:
    enum { IDD = IDD_HASH_CONFIG };
    
    CHashConfigDlg(HashConfig* pConfig);
    
    BEGIN_MSG_MAP(CHashConfigDlg)
        MESSAGE_HANDLER(WM_INITDIALOG, OnInitDialog)
        COMMAND_ID_HANDLER(IDOK, OnOK)
        COMMAND_ID_HANDLER(IDCANCEL, OnCancel)
        COMMAND_ID_HANDLER(IDC_BUTTON_CALC_ALL, OnCalculateAll)
        COMMAND_ID_HANDLER(IDC_BUTTON_CALC_SHA1, OnCalculateSHA1)
        COMMAND_ID_HANDLER(IDC_BUTTON_CALC_SHA256, OnCalculateSHA256)
        COMMAND_ID_HANDLER(IDC_BUTTON_LOAD_FILE, OnLoadFile)
    END_MSG_MAP()
    
private:
    HashConfig* m_pConfig;
    
    LRESULT OnInitDialog(UINT msg, WPARAM wParam, LPARAM lParam, BOOL& handled);
    LRESULT OnOK(WORD code, WORD id, HWND hwnd, BOOL& handled);
    LRESULT OnCancel(WORD code, WORD id, HWND hwnd, BOOL& handled);
    LRESULT OnCalculateAll(WORD code, WORD id, HWND hwnd, BOOL& handled);
    LRESULT OnCalculateSHA1(WORD code, WORD id, HWND hwnd, BOOL& handled);
    LRESULT OnCalculateSHA256(WORD code, WORD id, HWND hwnd, BOOL& handled);
    LRESULT OnLoadFile(WORD code, WORD id, HWND hwnd, BOOL& handled);
    
    void CalculateHashForPartition(bool sha1, bool sha256);
    void LoadFromDataToControls();
    void SaveFromControlsToData();
};
```

---

### Step 3: Create Main Application Entry Point

**File: `src/ODINM/OdinM.cpp`**
```cpp
#include "stdafx.h"
#include "resource.h"
#include "OdinMDlg.h"

CAppModule _Module;

int WINAPI _tWinMain(HINSTANCE hInstance, HINSTANCE, LPTSTR, int nCmdShow)
{
    HRESULT hRes = ::CoInitialize(NULL);
    ATLASSERT(SUCCEEDED(hRes));
    
    ::DefWindowProc(NULL, 0, 0, 0L);
    
    AtlInitCommonControls(ICC_BAR_CLASSES | ICC_LISTVIEW_CLASSES);
    
    hRes = _Module.Init(NULL, hInstance);
    ATLASSERT(SUCCEEDED(hRes));
    
    int nRet = 0;
    {
        COdinMDlg dlgMain;
        nRet = (int)dlgMain.DoModal();
    }
    
    _Module.Term();
    ::CoUninitialize();
    
    return nRet;
}
```

---

### Step 4: Create Resource Script

**File: `src/ODINM/OdinM.rc`**

This is a complex file. You can either:
1. Use Visual Studio's Resource Editor to create it visually
2. Copy and adapt from ODIN.rc and modify the dialog templates

**Minimal version:**
```rc
// Microsoft Visual C++ generated resource script.
#include "resource.h"
#include "windows.h"

/////////////////////////////////////////////////////////////////////////////
// English (United States) resources

LANGUAGE LANG_ENGLISH, SUBLANG_ENGLISH_US

/////////////////////////////////////////////////////////////////////////////
// Dialog

IDD_ODINM_MAIN DIALOGEX 0, 0, 600, 400
STYLE DS_SETFONT | DS_MODALFRAME | DS_FIXEDSYS | WS_POPUP | WS_CAPTION | WS_SYSMENU
CAPTION "OdinM - Multi-Drive Clone Tool"
FONT 8, "MS Shell Dlg", 400, 0, 0x1
BEGIN
    LTEXT           "Source Image:",IDC_STATIC,7,10,50,8
    EDITTEXT        IDC_EDIT_IMAGE_PATH,60,8,400,14,ES_AUTOHSCROLL
    PUSHBUTTON      "Browse...",IDC_BUTTON_BROWSE,465,8,50,14
    PUSHBUTTON      "Info",IDC_BUTTON_IMAGE_INFO,520,8,35,14
    
    CONTROL         "",IDC_LIST_DRIVES,"SysListView32",LVS_REPORT | WS_BORDER | WS_TABSTOP,7,30,586,150
    
    GROUPBOX        "Settings",IDC_STATIC,7,185,586,50
    CONTROL         "Auto-clone on device insertion",IDC_CHECK_AUTO_CLONE,"Button",BS_AUTOCHECKBOX | WS_TABSTOP,15,198,130,10
    CONTROL         "Verify hash after clone",IDC_CHECK_VERIFY_HASH,"Button",BS_AUTOCHECKBOX | WS_TABSTOP,15,213,100,10
    PUSHBUTTON      "Configure Hashes",IDC_BUTTON_CONFIG_HASH,120,211,80,14
    CONTROL         "Stop all on verification failure",IDC_CHECK_STOP_ON_FAIL,"Button",BS_AUTOCHECKBOX | WS_TABSTOP,210,213,120,10
    
    LTEXT           "Activity Log:",IDC_STATIC,7,240,50,8
    EDITTEXT        IDC_EDIT_LOG,7,250,586,90,ES_MULTILINE | ES_AUTOVSCROLL | ES_READONLY | WS_VSCROLL
    
    PUSHBUTTON      "Start All",IDC_BUTTON_START_ALL,7,350,60,20
    PUSHBUTTON      "Stop All",IDC_BUTTON_STOP_ALL,72,350,60,20
    PUSHBUTTON      "Clear Log",IDC_BUTTON_CLEAR_LOG,137,350,60,20
    PUSHBUTTON      "Export Results",IDC_BUTTON_EXPORT,202,350,70,20
    DEFPUSHBUTTON   "Close",IDOK,543,350,50,20
END

IDD_HASH_CONFIG DIALOGEX 0, 0, 400, 300
STYLE DS_SETFONT | DS_MODALFRAME | DS_FIXEDSYS | WS_POPUP | WS_CAPTION | WS_SYSMENU
CAPTION "Configure Hash Verification"
FONT 8, "MS Shell Dlg", 400, 0, 0x1
BEGIN
    LTEXT           "Image:",IDC_STATIC,7,10,30,8
    LTEXT           "",IDC_STATIC_IMAGE_NAME,40,10,353,8
    
    LTEXT           "Partition:",IDC_STATIC,7,25,40,8
    COMBOBOX        IDC_COMBO_PARTITION,50,23,200,100,CBS_DROPDOWNLIST | WS_VSCROLL | WS_TABSTOP
    
    GROUPBOX        "Expected Hash Values",IDC_STATIC,7,45,386,100
    LTEXT           "SHA-1 (40 hex chars):",IDC_STATIC,15,60,80,8
    EDITTEXT        IDC_EDIT_SHA1,15,70,360,14,ES_AUTOHSCROLL
    CONTROL         "Enable SHA-1",IDC_CHECK_ENABLE_SHA1,"Button",BS_AUTOCHECKBOX | WS_TABSTOP,15,88,60,10
    CONTROL         "Fail on mismatch",IDC_CHECK_FAIL_SHA1,"Button",BS_AUTOCHECKBOX | WS_TABSTOP,85,88,75,10
    
    LTEXT           "SHA-256 (64 hex chars):",IDC_STATIC,15,105,85,8
    EDITTEXT        IDC_EDIT_SHA256,15,115,360,14,ES_AUTOHSCROLL
    CONTROL         "Enable SHA-256",IDC_CHECK_ENABLE_SHA256,"Button",BS_AUTOCHECKBOX | WS_TABSTOP,15,133,70,10
    CONTROL         "Fail on mismatch",IDC_CHECK_FAIL_SHA256,"Button",BS_AUTOCHECKBOX | WS_TABSTOP,95,133,75,10
    
    GROUPBOX        "Calculate from Image",IDC_STATIC,7,150,386,60
    PUSHBUTTON      "Calculate All",IDC_BUTTON_CALC_ALL,15,165,70,20
    PUSHBUTTON      "Calculate SHA-1",IDC_BUTTON_CALC_SHA1,90,165,80,20
    PUSHBUTTON      "Calculate SHA-256",IDC_BUTTON_CALC_SHA256,175,165,85,20
    PUSHBUTTON      "Load from File",IDC_BUTTON_LOAD_FILE,265,165,70,20
    
    DEFPUSHBUTTON   "Save",IDOK,7,270,50,20
    PUSHBUTTON      "Cancel",IDCANCEL,62,270,50,20
END
```

---

### Step 5: Create Visual Studio Project File

**File: `OdinM.vcxproj`** (in root directory)

Copy from ODIN.vcxproj and modify:
- Change ProjectGuid to a new GUID
- Change RootNamespace to "OdinM"  
- Update file paths to src/ODINM/
- Add all OdinM source files

**Quick way:** 
1. Use Visual Studio
2. Right-click solution → Add → New Project → Empty C++ Project
3. Name it "OdinM"
4. Add existing files from src/ODINM/
5. Configure project properties to match ODIN (WTL paths, subsystem Windows, etc.)

---

### Step 6: Update Solution File

Add to `ODIN.sln` after the ODINC project section:

```
Project("{8BC9CEB8-8B4A-11D0-8D11-00A0C91BC942}") = "OdinM", "OdinM.vcxproj", "{NEW-GUID-HERE}"
EndProject
```

Add build configurations in the GlobalSection.

---

## Simplified Implementation for Quick Start

If you want to get something working quickly at home:

### Option A: Minimal Console Version First

Create a simple console app that:
1. Lists removable drives
2. Calls ODINC.exe to clone
3. Calculates SHA hashes
4. Compares results

**File: `src/ODINM/OdinM_Simple.cpp`**
```cpp
#include <windows.h>
#include <iostream>
#include <string>
#include <vector>

// Simple version - just core logic
int main() {
    std::wcout << L"OdinM - Simple Multi-Drive Clone Tool\n";
    std::wcout << L"=====================================\n\n";
    
    // TODO: Implement basic drive detection
    // TODO: Call ODINC.exe for each drive
    // TODO: Calculate hashes
    // TODO: Compare and report
    
    return 0;
}
```

### Option B: Use Existing ODIN Code

You can reuse a lot of code from ODIN:
- Drive detection: `DriveList.cpp`, `DriveUtil.cpp`
- INI file handling: `Config.cpp`, `IniWrapper.cpp`
- Process spawning patterns from `ODINDlg.cpp`

---

## Testing Plan

### Phase 1: Basic Functionality
1. Create project and get it to compile
2. Display main dialog
3. Browse for image file
4. Detect removable drives
5. Display drives in list view

### Phase 2: Cloning
1. Spawn ODINC.exe process
2. Monitor process completion
3. Handle multiple concurrent processes
4. Update UI with progress

### Phase 3: Hash Verification
1. Calculate SHA-1 from partition
2. Calculate SHA-256 from partition
3. Compare with expected values
4. Display results

### Phase 4: Full Integration
1. Auto-clone on device insertion
2. Save/load hash configurations
3. Export results to CSV
4. Error handling and logging

---

## Key Dependencies

**From Windows SDK:**
- `wincrypt.h` - For SHA hash calculation
- `winioctl.h` - For drive access
- `dbt.h` - For device change notifications

**From WTL 10.0:**
- Dialog classes
- List view control
- Common controls

**From ODIN:**
- Can reuse: DriveList, DriveUtil, Config, IniWrapper
- Reference: ODINDlg for process spawning patterns

---

## Build Configuration

**Project Properties to set:**
1. **C/C++ → General → Additional Include Directories:**
   - `$(ProjectDir)lib\WTL10\Include`

2. **C/C++ → Precompiled Headers:**
   - Use Precompiled Header: Yes
   - Precompiled Header File: `stdafx.h`

3. **Linker → System → SubSystem:**
   - Windows (/SUBSYSTEM:WINDOWS)

4. **Linker → Manifest → UAC Execution Level:**
   - requireAdministrator

5. **Linker → Input → Additional Dependencies:**
   - `shlwapi.lib`
   - `version.lib`
   - `crypt32.lib` (for CryptoAPI)

---

## Next Steps Summary

**To complete OdinM at home:**

1. ✅ Files already created - review them
2. ❌ Create HashCalculator.h/cpp (copy code above)
3. ❌ Create HashConfigDlg.h/cpp
4. ❌ Implement OdinMDlg.cpp (largest file)
5. ❌ Create OdinM.cpp (entry point - simple)
6. ❌ Create OdinM.rc (use Visual Studio Resource Editor)
7. ❌ Create OdinM.vcxproj (use VS to add project to solution)
8. ❌ Copy ODIN icon or create new one
9. ✅ Build and test incrementally

**Recommended approach:**
1. Start with minimal version that shows dialog
2. Add drive detection
3. Add ODINC spawning
4. Add hash calculation
5. Add full UI and features

**Estimated time:** 6-10 hours for full implementation

---

## Troubleshooting

**Common issues:**

1. **WTL headers not found**
   - Ensure lib/WTL10/Include is in include paths

2. **Linker errors about CryptoAPI**
   - Add `crypt32.lib` to linker dependencies

3. **Administrator rights needed**
   - Set UAC execution level to requireAdministrator

4. **ODINC.exe not found**
   - Ensure ODINC.exe is in same directory or provide full path

---

## Contact/Questions

For any questions or issues during implementation:
- Refer to ODIN source code for similar patterns
- Check WTL samples in lib/WTL10/Samples
- Review specification in ODINM_SPECIFICATION.md

**Good luck with the implementation!** 🚀


