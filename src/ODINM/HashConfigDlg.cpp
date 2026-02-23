/******************************************************************************

    OdinM - Multi-Drive Clone Tool
    HashConfigDlg.cpp - Hash configuration dialog implementation

******************************************************************************/

#include "stdafx.h"
#include "resource.h"
#include "HashConfigDlg.h"

// ---------------------------------------------------------------------------
// Constructor
// ---------------------------------------------------------------------------
CHashConfigDlg::CHashConfigDlg(HashConfig& config)
    : m_config(config)
{
}

// ---------------------------------------------------------------------------
// WM_INITDIALOG
// ---------------------------------------------------------------------------
LRESULT CHashConfigDlg::OnInitDialog(UINT /*msg*/, WPARAM /*wParam*/,
                                      LPARAM /*lParam*/, BOOL& handled)
{
    CenterWindow(GetParent());
    LoadConfigToControls();
    UpdateStatusLabels();
    handled = TRUE;
    return TRUE;   // let ATL set default focus
}

// ---------------------------------------------------------------------------
// IDOK - save and close
// ---------------------------------------------------------------------------
LRESULT CHashConfigDlg::OnOK(WORD /*code*/, WORD /*id*/, HWND /*hwnd*/, BOOL& handled)
{
    SaveControlsToConfig();
    EndDialog(IDOK);
    handled = TRUE;
    return 0;
}

// ---------------------------------------------------------------------------
// IDCANCEL - discard and close
// ---------------------------------------------------------------------------
LRESULT CHashConfigDlg::OnCancel(WORD /*code*/, WORD /*id*/, HWND /*hwnd*/, BOOL& handled)
{
    EndDialog(IDCANCEL);
    handled = TRUE;
    return 0;
}

// ---------------------------------------------------------------------------
// Calculate both SHA-1 and SHA-256 in one efficient pass
// ---------------------------------------------------------------------------
LRESULT CHashConfigDlg::OnCalculateAll(WORD /*code*/, WORD /*id*/, HWND /*hwnd*/, BOOL& handled)
{
    CalculateHashesFromImage(true, true);
    handled = TRUE;
    return 0;
}

// ---------------------------------------------------------------------------
// Calculate SHA-1 only
// ---------------------------------------------------------------------------
LRESULT CHashConfigDlg::OnCalculateSHA1(WORD /*code*/, WORD /*id*/, HWND /*hwnd*/, BOOL& handled)
{
    CalculateHashesFromImage(true, false);
    handled = TRUE;
    return 0;
}

// ---------------------------------------------------------------------------
// Calculate SHA-256 only
// ---------------------------------------------------------------------------
LRESULT CHashConfigDlg::OnCalculateSHA256(WORD /*code*/, WORD /*id*/, HWND /*hwnd*/, BOOL& handled)
{
    CalculateHashesFromImage(false, true);
    handled = TRUE;
    return 0;
}

// ---------------------------------------------------------------------------
// Browse for an ODIN image file
// ---------------------------------------------------------------------------
LRESULT CHashConfigDlg::OnLoadFile(WORD /*code*/, WORD /*id*/, HWND /*hwnd*/, BOOL& handled)
{
    OPENFILENAMEW ofn = {};
    wchar_t szFile[MAX_PATH] = {};

    // Seed with existing path if we have one
    if (!m_config.imagePath.empty())
        wcsncpy_s(szFile, m_config.imagePath.c_str(), _TRUNCATE);

    ofn.lStructSize  = sizeof(ofn);
    ofn.hwndOwner    = m_hWnd;
    ofn.lpstrFile    = szFile;
    ofn.nMaxFile     = _countof(szFile);
    ofn.lpstrFilter  = L"ODIN Image Files (*.img;*.odin)\0*.img;*.odin\0"
                       L"All Files (*.*)\0*.*\0\0";
    ofn.lpstrDefExt  = L"img";
    ofn.Flags        = OFN_FILEMUSTEXIST | OFN_HIDEREADONLY | OFN_PATHMUSTEXIST;

    if (GetOpenFileNameW(&ofn))
    {
        m_config.imagePath = szFile;

        // Show just the filename in the label
        std::wstring name = m_config.imagePath;
        size_t pos = name.find_last_of(L"\\/");
        if (pos != std::wstring::npos)
            name = name.substr(pos + 1);
        SetControlText(IDC_STATIC_IMAGE_NAME, name);
    }

    handled = TRUE;
    return 0;
}

// ---------------------------------------------------------------------------
// Core: open the image file and calculate the requested hashes.
//
// NOTE: Currently hashes the entire image file.  If partition-specific
//       hashing is needed, add ODIN file-header parsing here to derive
//       the correct byte offset/length for each partition.
// ---------------------------------------------------------------------------
void CHashConfigDlg::CalculateHashesFromImage(bool calcSHA1, bool calcSHA256)
{
    if (m_config.imagePath.empty())
    {
        MessageBox(
            L"Please select an image file first (Load File button).",
            L"No Image Selected", MB_ICONWARNING | MB_OK);
        return;
    }

    HANDLE hFile = CreateFileW(
        m_config.imagePath.c_str(),
        GENERIC_READ, FILE_SHARE_READ,
        NULL, OPEN_EXISTING,
        FILE_FLAG_SEQUENTIAL_SCAN, NULL);

    if (hFile == INVALID_HANDLE_VALUE)
    {
        MessageBox(
            L"Cannot open image file.\nCheck that the file exists and you have read access.",
            L"Open Failed", MB_ICONERROR | MB_OK);
        return;
    }

    LARGE_INTEGER fileSize = {};
    if (!GetFileSizeEx(hFile, &fileSize) || fileSize.QuadPart == 0)
    {
        CloseHandle(hFile);
        MessageBox(L"Cannot determine image file size.", L"Error", MB_ICONERROR | MB_OK);
        return;
    }

    // Show a wait cursor - hashing large files takes time
    HCURSOR hPrev = SetCursor(LoadCursor(NULL, IDC_WAIT));

    std::wstring sha1Result, sha256Result;
    bool ok = false;

    if (calcSHA1 && calcSHA256)
    {
        ok = CHashCalculator::CalculateBothHashes(
            hFile, 0, (ULONGLONG)fileSize.QuadPart,
            sha1Result, sha256Result);
    }
    else if (calcSHA1)
    {
        ok = CHashCalculator::CalculateSHA1(
            hFile, 0, (ULONGLONG)fileSize.QuadPart, sha1Result);
    }
    else if (calcSHA256)
    {
        ok = CHashCalculator::CalculateSHA256(
            hFile, 0, (ULONGLONG)fileSize.QuadPart, sha256Result);
    }

    CloseHandle(hFile);
    SetCursor(hPrev);

    if (!ok)
    {
        MessageBox(L"Hash calculation failed.", L"Error", MB_ICONERROR | MB_OK);
        return;
    }

    if (calcSHA1   && !sha1Result.empty())
        SetControlText(IDC_EDIT_SHA1,   sha1Result);
    if (calcSHA256 && !sha256Result.empty())
        SetControlText(IDC_EDIT_SHA256, sha256Result);

    UpdateStatusLabels();
}

// ---------------------------------------------------------------------------
// Populate all controls from m_config
// ---------------------------------------------------------------------------
void CHashConfigDlg::LoadConfigToControls()
{
    // Image name label (filename only, or placeholder)
    if (!m_config.imagePath.empty())
    {
        std::wstring name = m_config.imagePath;
        size_t pos = name.find_last_of(L"\\/");
        if (pos != std::wstring::npos)
            name = name.substr(pos + 1);
        SetControlText(IDC_STATIC_IMAGE_NAME, name);
    }
    else
    {
        SetControlText(IDC_STATIC_IMAGE_NAME, L"(no image selected)");
    }

    // Partition combo: populate entries 1-10 and select the configured one
    HWND hCombo = GetDlgItem(IDC_COMBO_PARTITION);
    SendMessage(hCombo, CB_RESETCONTENT, 0, 0);
    for (int i = 1; i <= 10; i++)
    {
        wchar_t buf[32];
        swprintf_s(buf, L"Partition %d", i);
        int idx = (int)SendMessage(hCombo, CB_ADDSTRING, 0, (LPARAM)buf);
        SendMessage(hCombo, CB_SETITEMDATA, (WPARAM)idx, (LPARAM)i);
    }
    int selIdx = m_config.partitionNumber - 1;
    if (selIdx < 0 || selIdx > 9) selIdx = 0;
    SendMessage(hCombo, CB_SETCURSEL, (WPARAM)selIdx, 0);

    // Hash value edit boxes
    SetControlText(IDC_EDIT_SHA1,   m_config.sha1Expected);
    SetControlText(IDC_EDIT_SHA256, m_config.sha256Expected);

    // Checkboxes
    SetCheckState(IDC_CHECK_ENABLE_SHA1,   m_config.sha1Enabled);
    SetCheckState(IDC_CHECK_ENABLE_SHA256, m_config.sha256Enabled);
    SetCheckState(IDC_CHECK_FAIL_SHA1,     m_config.failOnSha1Mismatch);
    SetCheckState(IDC_CHECK_FAIL_SHA256,   m_config.failOnSha256Mismatch);
}

// ---------------------------------------------------------------------------
// Save all controls back to m_config
// ---------------------------------------------------------------------------
void CHashConfigDlg::SaveControlsToConfig()
{
    // Partition number from combo selection
    HWND hCombo = GetDlgItem(IDC_COMBO_PARTITION);
    int idx = (int)SendMessage(hCombo, CB_GETCURSEL, 0, 0);
    if (idx != CB_ERR)
        m_config.partitionNumber = (int)SendMessage(hCombo, CB_GETITEMDATA, (WPARAM)idx, 0);

    // Hash strings
    m_config.sha1Expected   = GetControlText(IDC_EDIT_SHA1);
    m_config.sha256Expected = GetControlText(IDC_EDIT_SHA256);

    // Normalise to uppercase so comparisons are case-insensitive by default
    auto toUpper = [](std::wstring& s) {
        for (wchar_t& c : s)
            c = (wchar_t)towupper(c);
    };
    toUpper(m_config.sha1Expected);
    toUpper(m_config.sha256Expected);

    // Checkboxes
    m_config.sha1Enabled          = GetCheckState(IDC_CHECK_ENABLE_SHA1);
    m_config.sha256Enabled        = GetCheckState(IDC_CHECK_ENABLE_SHA256);
    m_config.failOnSha1Mismatch   = GetCheckState(IDC_CHECK_FAIL_SHA1);
    m_config.failOnSha256Mismatch = GetCheckState(IDC_CHECK_FAIL_SHA256);
}

// ---------------------------------------------------------------------------
// Update status labels to reflect whether hashes have been configured
// ---------------------------------------------------------------------------
void CHashConfigDlg::UpdateStatusLabels()
{
    std::wstring sha1Text  = GetControlText(IDC_EDIT_SHA1);
    std::wstring sha256Text = GetControlText(IDC_EDIT_SHA256);

    SetControlText(IDC_STATIC_STATUS_SHA1,
        sha1Text.empty()   ? L"Not configured" : L"Hash set");
    SetControlText(IDC_STATIC_STATUS_SHA256,
        sha256Text.empty() ? L"Not configured" : L"Hash set");
}

// ---------------------------------------------------------------------------
// Helper: read text from a dialog control
// ---------------------------------------------------------------------------
std::wstring CHashConfigDlg::GetControlText(int controlId)
{
    wchar_t buf[1024] = {};
    GetDlgItemText(controlId, buf, _countof(buf));
    return std::wstring(buf);
}

// ---------------------------------------------------------------------------
// Helper: query a checkbox state
// ---------------------------------------------------------------------------
bool CHashConfigDlg::GetCheckState(int controlId)
{
    return (IsDlgButtonChecked(controlId) == BST_CHECKED);
}

// ---------------------------------------------------------------------------
// Helper: write text to a dialog control
// ---------------------------------------------------------------------------
void CHashConfigDlg::SetControlText(int controlId, const std::wstring& text)
{
    SetDlgItemText(controlId, text.c_str());
}

// ---------------------------------------------------------------------------
// Helper: set or clear a checkbox
// ---------------------------------------------------------------------------
void CHashConfigDlg::SetCheckState(int controlId, bool checked)
{
    CheckDlgButton(controlId, checked ? BST_CHECKED : BST_UNCHECKED);
}
