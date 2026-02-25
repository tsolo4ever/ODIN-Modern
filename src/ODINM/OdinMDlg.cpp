/******************************************************************************
    OdinM - Multi-Drive Clone Tool
    OdinMDlg.cpp - Main dialog implementation
******************************************************************************/
#include "stdafx.h"
#include "resource.h"
#include "OdinMDlg.h"
#include "HashConfigDlg.h"
#include "HashCalculator.h"
#include <dbt.h>

// ListView column indices
enum { COL_SLOT=0, COL_DRIVE, COL_NAME, COL_SIZE, COL_STATUS, COL_PROGRESS, COL_SHA1, COL_SHA256, COL_COUNT };
static const UINT_PTR POLL_TIMER_ID = 1;
static const UINT     POLL_INTERVAL = 2000;

// --- Constructor/Destructor ---
COdinMDlg::COdinMDlg() : m_autoCloneEnabled(false), m_maxConcurrent(2), m_timer(0)
{
    for (int i = 0; i < 5; i++)
        m_driveSlots.emplace_back(std::make_unique<CDriveSlot>(i + 1));
}
COdinMDlg::~COdinMDlg() { }

// --- OnDestroy ---
// Kill the poll timer here while m_hWnd is still valid.
// Never call KillTimer() from the destructor — the window is gone by then.
LRESULT COdinMDlg::OnDestroy(UINT, WPARAM, LPARAM, BOOL& handled)
{
    if (m_timer) { ::KillTimer(m_hWnd, m_timer); m_timer = 0; }
    if (m_darkBgBrush)   { ::DeleteObject(m_darkBgBrush);   m_darkBgBrush   = nullptr; }
    if (m_darkEditBrush) { ::DeleteObject(m_darkEditBrush); m_darkEditBrush = nullptr; }
    handled = FALSE;   // let DefWindowProc process WM_DESTROY too
    return 0;
}

// --- OnInitDialog ---
LRESULT COdinMDlg::OnInitDialog(UINT, WPARAM, LPARAM, BOOL& handled)
{
    m_driveList.Attach(GetDlgItem(IDC_LIST_DRIVES));
    m_imagePathEdit.Attach(GetDlgItem(IDC_EDIT_IMAGE_PATH));
    m_logEdit.Attach(GetDlgItem(IDC_EDIT_LOG));
    m_autoCloneCheck.Attach(GetDlgItem(IDC_CHECK_AUTO_CLONE));
    m_verifyHashCheck.Attach(GetDlgItem(IDC_CHECK_VERIFY_HASH));
    m_stopOnFailCheck.Attach(GetDlgItem(IDC_CHECK_STOP_ON_FAIL));
    m_maxConcurrentCombo.Attach(GetDlgItem(IDC_COMBO_MAX_CONCURRENT));
    m_statusStatic.Attach(GetDlgItem(IDC_STATIC_STATUS));
    InitializeDriveList();
    InitializeControls();
    LoadSettings();
    RefreshDrives();
    UpdateDriveList();
    UpdateStatus();
    m_timer = SetTimer(POLL_TIMER_ID, POLL_INTERVAL);
    CenterWindow();
    Log(L"OdinM started. Ready.");
    ApplyDarkMode(m_hWnd);       // create brushes before first paint
    PostMessage(WM_APP + 100, 0, 0);   // deferred: repaint after window is fully visible
    handled = TRUE;
    return TRUE;
}

// --- InitializeDriveList ---
void COdinMDlg::InitializeDriveList()
{
    m_driveList.SetExtendedListViewStyle(LVS_EX_FULLROWSELECT | LVS_EX_GRIDLINES | LVS_EX_DOUBLEBUFFER);
    struct { const wchar_t* title; int width; } cols[COL_COUNT] = {
        {L"Slot",     40}, {L"Drive",    55}, {L"Name",   110},
        {L"Size",     65}, {L"Status",   75}, {L"Progress", 60},
        {L"SHA-1",   185}, {L"SHA-256", 185}
    };
    for (int i = 0; i < COL_COUNT; i++) {
        LVCOLUMN lvc = {};
        lvc.mask = LVCF_TEXT | LVCF_WIDTH | LVCF_SUBITEM;
        lvc.pszText = const_cast<wchar_t*>(cols[i].title);
        lvc.cx = cols[i].width;
        lvc.iSubItem = i;
        m_driveList.InsertColumn(i, &lvc);
    }
    for (int i = 0; i < 5; i++) {
        LVITEM lvi = {}; lvi.mask = LVIF_TEXT; lvi.iItem = i;
        wchar_t buf[4]; swprintf_s(buf, L"%d", i + 1); lvi.pszText = buf;
        m_driveList.InsertItem(&lvi);
    }
}

// --- InitializeControls ---
void COdinMDlg::InitializeControls()
{
    for (int i = 1; i <= 5; i++) {
        wchar_t buf[4]; swprintf_s(buf, L"%d", i);
        m_maxConcurrentCombo.AddString(buf);
    }
    m_maxConcurrentCombo.SetCurSel(m_maxConcurrent - 1);
    m_logEdit.SetReadOnly(TRUE);
}

// --- LoadSettings ---
void COdinMDlg::LoadSettings()
{
    wchar_t exe[MAX_PATH]; GetModuleFileNameW(NULL, exe, MAX_PATH);
    PathRemoveFileSpecW(exe);
    std::wstring ini = std::wstring(exe) + L"\\OdinM.ini";

    wchar_t buf[MAX_PATH] = {};
    GetPrivateProfileStringW(L"Settings", L"ImagePath", L"", buf, MAX_PATH, ini.c_str());
    if (buf[0]) {
        m_imagePath = buf;
        m_imagePathEdit.SetWindowText(buf);
        m_hashConfig.imagePath = m_imagePath;
        LoadHashConfig(m_imagePath);
    }
    m_maxConcurrent = GetPrivateProfileIntW(L"Settings", L"MaxConcurrent", 2, ini.c_str());
    if (m_maxConcurrent < 1) m_maxConcurrent = 1;
    if (m_maxConcurrent > 5) m_maxConcurrent = 5;
    m_maxConcurrentCombo.SetCurSel(m_maxConcurrent - 1);
    m_autoCloneEnabled = GetPrivateProfileIntW(L"Settings", L"AutoClone", 0, ini.c_str()) != 0;
    m_autoCloneCheck.SetCheck(m_autoCloneEnabled ? BST_CHECKED : BST_UNCHECKED);
}

// --- SaveSettings ---
void COdinMDlg::SaveSettings()
{
    wchar_t exe[MAX_PATH]; GetModuleFileNameW(NULL, exe, MAX_PATH);
    PathRemoveFileSpecW(exe);
    std::wstring ini = std::wstring(exe) + L"\\OdinM.ini";
    WritePrivateProfileStringW(L"Settings", L"ImagePath",
        m_imagePath.empty() ? L"" : m_imagePath.c_str(), ini.c_str());
    int idx = m_maxConcurrentCombo.GetCurSel();
    if (idx != CB_ERR) m_maxConcurrent = idx + 1;
    wchar_t buf[4]; swprintf_s(buf, L"%d", m_maxConcurrent);
    WritePrivateProfileStringW(L"Settings", L"MaxConcurrent", buf, ini.c_str());
    WritePrivateProfileStringW(L"Settings", L"AutoClone",
        m_autoCloneEnabled ? L"1" : L"0", ini.c_str());
}

// --- OnDeviceChange ---
LRESULT COdinMDlg::OnDeviceChange(UINT, WPARAM wParam, LPARAM, BOOL& handled)
{
    if (wParam == DBT_DEVICEARRIVAL || wParam == DBT_DEVICEREMOVECOMPLETE) {
        Sleep(600);
        RefreshDrives();
        UpdateDriveList();
        UpdateStatus();
        Log(L"Device change detected.");
    }
    handled = FALSE;
    return TRUE;
}

// --- OnTimer ---
LRESULT COdinMDlg::OnTimer(UINT, WPARAM wParam, LPARAM, BOOL& handled)
{
    if (wParam == POLL_TIMER_ID) {
        DetectNewDrives();
        // Check if any Cloning process finished
        for (int i = 0; i < (int)m_driveSlots.size(); i++) {
            CDriveSlot* slot = m_driveSlots[i].get();
            if (slot->GetStatus() == CloneStatus::Cloning && slot->GetProcessId()) {
                HANDLE hProc = OpenProcess(PROCESS_QUERY_INFORMATION | SYNCHRONIZE,
                                           FALSE, slot->GetProcessId());
                if (hProc) {
                    DWORD exit = STILL_ACTIVE;
                    GetExitCodeProcess(hProc, &exit);
                    CloseHandle(hProc);
                    if (exit != STILL_ACTIVE) {
                        slot->SetProcessId(0);
                        if (exit == 0) {
                            slot->SetProgress(100);
                            LogDrive(i, L"Clone complete.");
                            if (m_verifyHashCheck.GetCheck() == BST_CHECKED)
                                VerifyDrive(i);
                            else
                                slot->SetStatus(CloneStatus::Complete);
                        } else {
                            slot->SetStatus(CloneStatus::Failed);
                            LogDrive(i, L"Clone failed (exit " + std::to_wstring(exit) + L").");
                        }
                    }
                } else {
                    slot->SetProcessId(0);
                    slot->SetProgress(100);
                    slot->SetStatus(CloneStatus::Complete);
                    VerifyDrive(i);
                }
            }
        }
        UpdateDriveList();
        UpdateStatus();
    }
    handled = TRUE;
    return 0;
}

// --- OnOK ---
LRESULT COdinMDlg::OnOK(WORD, WORD, HWND, BOOL& handled)
{
    for (auto& s : m_driveSlots)
        if (s->IsActive()) {
            MessageBox(L"Please stop all active operations before closing.", L"Busy", MB_ICONWARNING);
            handled = TRUE; return 0;
        }
    SaveSettings(); EndDialog(IDOK); handled = TRUE; return 0;
}

// --- OnCancel ---
LRESULT COdinMDlg::OnCancel(WORD c, WORD i, HWND h, BOOL& handled) { return OnOK(c,i,h,handled); }

// --- OnBrowse ---
LRESULT COdinMDlg::OnBrowse(WORD, WORD, HWND, BOOL& handled)
{
    OPENFILENAMEW ofn = {}; wchar_t szFile[MAX_PATH] = {};
    if (!m_imagePath.empty()) wcsncpy_s(szFile, m_imagePath.c_str(), _TRUNCATE);
    ofn.lStructSize = sizeof(ofn); ofn.hwndOwner = m_hWnd;
    ofn.lpstrFile = szFile; ofn.nMaxFile = _countof(szFile);
    ofn.lpstrFilter = L"ODIN Image Files (*.img;*.odin)\0*.img;*.odin\0All Files (*.*)\0*.*\0\0";
    ofn.lpstrDefExt = L"img"; ofn.Flags = OFN_FILEMUSTEXIST | OFN_HIDEREADONLY | OFN_PATHMUSTEXIST;
    if (GetOpenFileNameW(&ofn)) {
        m_imagePath = szFile;
        m_imagePathEdit.SetWindowText(szFile);
        m_hashConfig.imagePath = m_imagePath;
        LoadHashConfig(m_imagePath);
        Log(std::wstring(L"Image selected: ") + szFile);
    }
    handled = TRUE; return 0;
}

// --- OnImageInfo ---
LRESULT COdinMDlg::OnImageInfo(WORD, WORD, HWND, BOOL& handled)
{
    if (m_imagePath.empty()) { MessageBox(L"No image file selected.", L"Info", MB_ICONINFORMATION); handled=TRUE; return 0; }
    WIN32_FILE_ATTRIBUTE_DATA fa = {};
    if (!GetFileAttributesExW(m_imagePath.c_str(), GetFileExInfoStandard, &fa)) {
        MessageBox(L"Cannot read image file.", L"Error", MB_ICONERROR); handled=TRUE; return 0;
    }
    ULONGLONG sz = ((ULONGLONG)fa.nFileSizeHigh << 32) | fa.nFileSizeLow;
    wchar_t msg[512];
    swprintf_s(msg, L"Path:    %s\nSize:    %.2f GB\n\nSHA-1:   %s\nSHA-256: %s",
        m_imagePath.c_str(), sz / (1024.0*1024.0*1024.0),
        m_hashConfig.sha1Expected.empty()   ? L"(not set)" : m_hashConfig.sha1Expected.c_str(),
        m_hashConfig.sha256Expected.empty() ? L"(not set)" : m_hashConfig.sha256Expected.c_str());
    MessageBox(msg, L"Image Information", MB_ICONINFORMATION);
    handled = TRUE; return 0;
}

// --- OnConfigureHash ---
LRESULT COdinMDlg::OnConfigureHash(WORD, WORD, HWND, BOOL& handled)
{
    m_hashConfig.imagePath = m_imagePath;
    CHashConfigDlg dlg(m_hashConfig);
    if (dlg.DoModal(m_hWnd) == IDOK) {
        SaveHashConfig(m_imagePath);
        Log(L"Hash configuration updated.");
    }
    handled = TRUE; return 0;
}

// --- OnStartAll ---
LRESULT COdinMDlg::OnStartAll(WORD, WORD, HWND, BOOL& handled)
{
    if (!IsValidImageFile(m_imagePath)) {
        MessageBox(L"Please select a valid ODIN image file first.", L"No Image", MB_ICONWARNING);
        handled = TRUE; return 0;
    }
    int idx = m_maxConcurrentCombo.GetCurSel();
    if (idx != CB_ERR) m_maxConcurrent = idx + 1;
    int started = 0;
    for (int i = 0; i < (int)m_driveSlots.size() && started < m_maxConcurrent; i++)
        if (m_driveSlots[i]->GetStatus() == CloneStatus::Ready) { StartClone(i); started++; }
    if (started == 0)
        MessageBox(L"No drives are ready to clone.", L"Nothing to do", MB_ICONINFORMATION);
    handled = TRUE; return 0;
}

// --- OnStopAll ---
LRESULT COdinMDlg::OnStopAll(WORD, WORD, HWND, BOOL& handled)
{
    for (int i = 0; i < (int)m_driveSlots.size(); i++)
        if (m_driveSlots[i]->IsActive()) StopClone(i);
    handled = TRUE; return 0;
}

// --- OnClearLog ---
LRESULT COdinMDlg::OnClearLog(WORD, WORD, HWND, BOOL& handled) { m_logEdit.SetWindowText(L""); handled=TRUE; return 0; }

// --- OnExport ---
LRESULT COdinMDlg::OnExport(WORD, WORD, HWND, BOOL& handled)
{
    OPENFILENAMEW ofn = {}; wchar_t szFile[MAX_PATH] = L"OdinM_Results.csv";
    ofn.lStructSize = sizeof(ofn); ofn.hwndOwner = m_hWnd;
    ofn.lpstrFile = szFile; ofn.nMaxFile = _countof(szFile);
    ofn.lpstrFilter = L"CSV Files (*.csv)\0*.csv\0All Files (*.*)\0*.*\0\0";
    ofn.lpstrDefExt = L"csv"; ofn.Flags = OFN_OVERWRITEPROMPT;
    if (!GetSaveFileNameW(&ofn)) { handled = TRUE; return 0; }
    HANDLE hf = CreateFileW(szFile, GENERIC_WRITE, 0, NULL, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    if (hf == INVALID_HANDLE_VALUE) { MessageBox(L"Cannot create file.", L"Error", MB_ICONERROR); handled=TRUE; return 0; }
    DWORD w;
    const wchar_t* hdr = L"\xFEFFSlot,Drive,Name,Size,Status,SHA-1,SHA-256\r\n";
    WriteFile(hf, hdr, (DWORD)(wcslen(hdr)*sizeof(wchar_t)), &w, NULL);
    for (auto& s : m_driveSlots) {
        const VerificationResult& vr = s->GetVerificationResult();
        wchar_t ln[512];
        swprintf_s(ln, L"%d,%s,%s,%s,%s,%s,%s\r\n",
            s->GetSlotNumber(), s->GetDriveLetter().c_str(), s->GetDriveName().c_str(),
            s->GetDriveSizeString().c_str(), s->GetStatusString().c_str(),
            vr.sha1Value.empty()   ? L"-" : (vr.sha1Pass   ? L"PASS" : L"FAIL"),
            vr.sha256Value.empty() ? L"-" : (vr.sha256Pass ? L"PASS" : L"FAIL"));
        WriteFile(hf, ln, (DWORD)(wcslen(ln)*sizeof(wchar_t)), &w, NULL);
    }
    CloseHandle(hf);
    Log(std::wstring(L"Exported to: ") + szFile);
    handled = TRUE; return 0;
}

// --- OnAutoCloneChanged ---
LRESULT COdinMDlg::OnAutoCloneChanged(WORD, WORD, HWND, BOOL& handled)
{
    m_autoCloneEnabled = (m_autoCloneCheck.GetCheck() == BST_CHECKED);
    Log(m_autoCloneEnabled ? L"Auto-clone enabled." : L"Auto-clone disabled.");
    handled = TRUE; return 0;
}

// --- RefreshDrives ---
void COdinMDlg::RefreshDrives()
{
    for (auto& s : m_driveSlots) s->ClearDrive();
    DWORD mask = GetLogicalDrives(); int slot = 0;
    for (int i = 0; i < 26 && slot < (int)m_driveSlots.size(); i++) {
        if (!(mask & (1u << i))) continue;
        wchar_t root[8]; swprintf_s(root, L"%c:\\", L'A' + i);
        if (GetDriveTypeW(root) != DRIVE_REMOVABLE) continue;
        std::wstring letter(1, L'A' + i); letter += L":";
        m_driveSlots[slot]->SetDrive(letter, GetDriveName(root), GetDriveSize(letter));
        slot++;
    }
}

// --- DetectNewDrives ---
void COdinMDlg::DetectNewDrives()
{
    DWORD mask = GetLogicalDrives();
    for (int i = 0; i < 26; i++) {
        if (!(mask & (1u << i))) continue;
        wchar_t root[8]; swprintf_s(root, L"%c:\\", L'A' + i);
        if (GetDriveTypeW(root) != DRIVE_REMOVABLE) continue;
        std::wstring letter(1, L'A' + i); letter += L":";
        bool tracked = false;
        for (auto& s : m_driveSlots) if (s->GetDriveLetter() == letter) { tracked = true; break; }
        if (!tracked) {
            for (int j = 0; j < (int)m_driveSlots.size(); j++) {
                if (m_driveSlots[j]->IsEmpty()) {
                    m_driveSlots[j]->SetDrive(letter, GetDriveName(root), GetDriveSize(letter));
                    LogDrive(j, L"Drive inserted: " + letter);
                    if (m_autoCloneEnabled && IsValidImageFile(m_imagePath)) StartClone(j);
                    break;
                }
            }
        }
    }
}

// --- StartClone ---
void COdinMDlg::StartClone(int idx)
{
    CDriveSlot* slot = m_driveSlots[idx].get();
    if (!slot || slot->IsEmpty() || slot->IsActive()) return;
    wchar_t exe[MAX_PATH]; GetModuleFileNameW(NULL, exe, MAX_PATH); PathRemoveFileSpecW(exe);
    std::wstring cmd = L"\"" + std::wstring(exe) + L"\\ODINC.exe\""
        + L" --source \"" + m_imagePath + L"\" --target " + slot->GetDriveLetter();
    STARTUPINFOW si = {}; PROCESS_INFORMATION pi = {}; si.cb = sizeof(si);
    if (CreateProcessW(NULL, &cmd[0], NULL, NULL, FALSE, CREATE_NO_WINDOW, NULL, NULL, &si, &pi)) {
        slot->SetStatus(CloneStatus::Cloning); slot->SetProgress(0); slot->SetProcessId(pi.dwProcessId);
        CloseHandle(pi.hThread); CloseHandle(pi.hProcess);
        LogDrive(idx, L"Clone started to " + slot->GetDriveLetter());
    } else {
        slot->SetStatus(CloneStatus::Failed);
        LogDrive(idx, L"Failed to launch ODINC.exe (err " + std::to_wstring(GetLastError()) + L")");
    }
    UpdateDriveList();
}

// --- StopClone ---
void COdinMDlg::StopClone(int idx)
{
    CDriveSlot* slot = m_driveSlots[idx].get();
    if (!slot || !slot->IsActive()) return;
    if (slot->GetProcessId()) {
        HANDLE h = OpenProcess(PROCESS_TERMINATE, FALSE, slot->GetProcessId());
        if (h) { TerminateProcess(h, 1); CloseHandle(h); }
    }
    slot->SetStatus(CloneStatus::Stopped); slot->SetProcessId(0);
    LogDrive(idx, L"Clone stopped."); UpdateDriveList();
}

// --- UpdateDriveList ---
void COdinMDlg::UpdateDriveList()
{
    for (int i = 0; i < (int)m_driveSlots.size(); i++) {
        CDriveSlot* s = m_driveSlots[i].get();
        const VerificationResult& vr = s->GetVerificationResult();
        auto sha = [](bool empty, bool pass, const std::wstring& v) -> std::wstring {
            if (empty) return L"-";
            return (pass ? L"PASS: " : L"FAIL: ") + v;
        };
        m_driveList.SetItemText(i, COL_DRIVE,    s->GetDriveLetter().c_str());
        m_driveList.SetItemText(i, COL_NAME,     s->GetDriveName().c_str());
        m_driveList.SetItemText(i, COL_SIZE,     s->GetDriveSizeString().c_str());
        m_driveList.SetItemText(i, COL_STATUS,   s->GetStatusString().c_str());
        m_driveList.SetItemText(i, COL_PROGRESS, s->GetProgressString().c_str());
        m_driveList.SetItemText(i, COL_SHA1,   sha(vr.sha1Value.empty(),   vr.sha1Pass,   vr.sha1Value).c_str());
        m_driveList.SetItemText(i, COL_SHA256, sha(vr.sha256Value.empty(), vr.sha256Pass, vr.sha256Value).c_str());
    }
}

// --- UpdateStatus ---
void COdinMDlg::UpdateStatus()
{
    int active=0, complete=0, failed=0;
    for (auto& s : m_driveSlots) {
        if (s->IsActive())   active++;
        if (s->IsComplete()) complete++;
        if (s->IsFailed())   failed++;
    }
    wchar_t buf[128]; swprintf_s(buf, L"Active: %d  |  Complete: %d  |  Failed: %d", active, complete, failed);
    m_statusStatic.SetWindowText(buf);
}

// --- Log ---
void COdinMDlg::Log(const std::wstring& msg)
{
    std::wstring entry = L"[" + GetTimeString() + L"] " + msg + L"\r\n";
    int len = m_logEdit.GetWindowTextLength();
    m_logEdit.SetSel(len, len);
    m_logEdit.ReplaceSel(entry.c_str());
}

// --- LogDrive ---
void COdinMDlg::LogDrive(int slot, const std::wstring& msg) { Log(L"Slot " + std::to_wstring(slot+1) + L": " + msg); }

// --- LoadHashConfig ---
bool COdinMDlg::LoadHashConfig(const std::wstring& imagePath)
{
    if (imagePath.empty()) return false;
    std::wstring cfg = imagePath + L".hashcfg";
    HANDLE hf = CreateFileW(cfg.c_str(), GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL);
    if (hf == INVALID_HANDLE_VALUE) return false;
    char buf[4096] = {}; DWORD read = 0;
    ReadFile(hf, buf, sizeof(buf)-1, &read, NULL); CloseHandle(hf);
    std::string text(buf, read);
    auto parse = [&](const std::string& key) -> std::wstring {
        size_t p = text.find(key + "="); if (p == std::string::npos) return L"";
        size_t e = text.find('\n', p);
        std::string v = text.substr(p + key.size() + 1, e == std::string::npos ? std::string::npos : e - p - key.size() - 1);
        if (!v.empty() && v.back() == '\r') v.pop_back();
        return std::wstring(v.begin(), v.end());
    };
    m_hashConfig.sha1Expected   = parse("SHA1");
    m_hashConfig.sha256Expected = parse("SHA256");
    return true;
}

// --- SaveHashConfig ---
bool COdinMDlg::SaveHashConfig(const std::wstring& imagePath)
{
    if (imagePath.empty()) return false;
    std::wstring cfg = imagePath + L".hashcfg";
    HANDLE hf = CreateFileW(cfg.c_str(), GENERIC_WRITE, 0, NULL, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    if (hf == INVALID_HANDLE_VALUE) return false;
    // Hash strings are pure ASCII hex (0-9, a-f) — explicit cast is lossless
    auto n = [](const std::wstring& w) {
        std::string s;
        s.reserve(w.size());
        for (wchar_t c : w)
            s += static_cast<char>(c);
        return s;
    };
    std::string content = "SHA1="   + n(m_hashConfig.sha1Expected)   + "\r\n"
                        + "SHA256=" + n(m_hashConfig.sha256Expected) + "\r\n";
    DWORD w; WriteFile(hf, content.c_str(), (DWORD)content.size(), &w, NULL);
    CloseHandle(hf); return true;
}

// --- VerifyDrive ---
void COdinMDlg::VerifyDrive(int idx)
{
    CDriveSlot* slot = m_driveSlots[idx].get();
    if (!slot || slot->IsEmpty()) return;
    slot->SetStatus(CloneStatus::Verifying);
    LogDrive(idx, L"Verifying hashes...");
    // Open the physical drive device
    std::wstring dev = L"\\\\.\\PhysicalDrive";  // fallback — prefer drive letter
    std::wstring drv = L"\\\\.\\";
    drv += slot->GetDriveLetter()[0];  // e.g. \\.\F:
    drv += L":";
    HANDLE hDrive = CreateFileW(drv.c_str(), GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE,
                                NULL, OPEN_EXISTING, FILE_FLAG_SEQUENTIAL_SCAN, NULL);
    if (hDrive == INVALID_HANDLE_VALUE) {
        slot->SetStatus(CloneStatus::Failed);
        LogDrive(idx, L"Cannot open drive for verification.");
        return;
    }
    LARGE_INTEGER driveSize = {};
    if (!GetFileSizeEx(hDrive, &driveSize)) driveSize.QuadPart = (LONGLONG)slot->GetDriveSize();
    VerificationResult vr;
    bool ok = CHashCalculator::CalculateBothHashes(hDrive, 0, (ULONGLONG)driveSize.QuadPart,
                                                   vr.sha1Value, vr.sha256Value);
    CloseHandle(hDrive);
    if (!ok) { slot->SetStatus(CloneStatus::Failed); LogDrive(idx, L"Hash calculation failed."); return; }
    vr.sha1Pass   = !m_hashConfig.sha1Expected.empty()   && vr.sha1Value   == m_hashConfig.sha1Expected;
    vr.sha256Pass = !m_hashConfig.sha256Expected.empty() && vr.sha256Value == m_hashConfig.sha256Expected;
    bool sha1ok   = m_hashConfig.sha1Expected.empty()   || vr.sha1Pass;
    bool sha256ok = m_hashConfig.sha256Expected.empty() || vr.sha256Pass;
    vr.overallPass = sha1ok && sha256ok;
    slot->SetVerificationResult(vr);
    slot->SetStatus(vr.overallPass ? CloneStatus::Complete : CloneStatus::Failed);
    LogDrive(idx, std::wstring(L"Verification ") + (vr.overallPass ? L"PASSED." : L"FAILED."));
    if (!vr.overallPass && m_stopOnFailCheck.GetCheck() == BST_CHECKED) {
        Log(L"Stop-on-fail: halting remaining operations.");
        for (int j = 0; j < (int)m_driveSlots.size(); j++) if (m_driveSlots[j]->IsActive()) StopClone(j);
    }
}

// --- GetTimeString ---
std::wstring COdinMDlg::GetTimeString()
{
    SYSTEMTIME st; GetLocalTime(&st);
    wchar_t buf[16]; swprintf_s(buf, L"%02d:%02d:%02d", st.wHour, st.wMinute, st.wSecond);
    return buf;
}

// --- IsValidImageFile ---
bool COdinMDlg::IsValidImageFile(const std::wstring& p) { return !p.empty() && GetFileAttributesW(p.c_str()) != INVALID_FILE_ATTRIBUTES; }

// --- GetDriveSize ---
ULONGLONG COdinMDlg::GetDriveSize(const std::wstring& letter)
{
    std::wstring dev = L"\\\\.\\"; dev += letter[0]; dev += L":";
    HANDLE h = CreateFileW(dev.c_str(), GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE,
                           NULL, OPEN_EXISTING, 0, NULL);
    if (h == INVALID_HANDLE_VALUE) return 0;
    GET_LENGTH_INFORMATION gli = {}; DWORD ret = 0;
    DeviceIoControl(h, IOCTL_DISK_GET_LENGTH_INFO, NULL, 0, &gli, sizeof(gli), &ret, NULL);
    CloseHandle(h);
    return (ULONGLONG)gli.Length.QuadPart;
}

// --- GetDriveName ---
std::wstring COdinMDlg::GetDriveName(const std::wstring& root)
{
    wchar_t label[MAX_PATH] = {};
    GetVolumeInformationW(root.c_str(), label, MAX_PATH, NULL, NULL, NULL, NULL, 0);
    return label[0] ? std::wstring(label) : L"Removable";
}

// ── Dark mode ────────────────────────────────────────────────────────────────
// Title bar:  DwmSetWindowAttribute (loaded at runtime via dwmapi.dll)
// Client area: AllowDarkModeForWindow (uxtheme ordinal 133) + WM_CTLCOLOR* +
//              SetWindowTheme on native controls.
// GetSysColor(COLOR_WINDOW/WINDOWTEXT/BTNFACE/BTNTEXT) returns correct dark
// values once SetPreferredAppMode(AllowDark) is called in _tWinMain.

static bool IsDarkModeEnabled() {
    // ShouldAppsUseDarkMode (uxtheme ordinal 132): only trust TRUE; always
    // fall through to the registry on FALSE (ordinal may be wrong/inactive).
    typedef BOOL (WINAPI* PFN_ShouldDark)();
    static bool s_tried = false;
    static PFN_ShouldDark s_pfn = nullptr;
    if (!s_tried) {
        s_tried = true;
        HMODULE h = ::GetModuleHandleW(L"uxtheme.dll");
        if (h) s_pfn = (PFN_ShouldDark)::GetProcAddress(h, MAKEINTRESOURCEA(132));
    }
    if (s_pfn && s_pfn() != FALSE)
        return true;

    // Definitive ground truth: AppsUseLightTheme = 0 means dark.
    DWORD value = 1, size = sizeof(value);
    HKEY hKey = NULL;
    if (RegOpenKeyExW(HKEY_CURRENT_USER,
          L"Software\\Microsoft\\Windows\\CurrentVersion\\Themes\\Personalize",
          0, KEY_QUERY_VALUE, &hKey) == ERROR_SUCCESS) {
        RegQueryValueExW(hKey, L"AppsUseLightTheme", nullptr, nullptr,
                         reinterpret_cast<LPBYTE>(&value), &size);
        RegCloseKey(hKey);
    }
    return value == 0;
}

using PFN_AllowDarkM = BOOL (WINAPI*)(HWND, BOOL);
struct DarkEnumCtxM { PFN_AllowDarkM fn; BOOL dark; };
static BOOL CALLBACK s_AllowDarkChildM(HWND child, LPARAM lp) {
    auto& ctx = *reinterpret_cast<DarkEnumCtxM*>(lp);
    if (ctx.fn) ctx.fn(child, ctx.dark);
    ::SendMessage(child, WM_THEMECHANGED, 0, 0);
    return TRUE;
}

// EnumChildWindows callback: apply the correct uxtheme subapp string per control class.
using PFN_SwtM = HRESULT (WINAPI*)(HWND, LPCWSTR, LPCWSTR);
struct ThemeEnumCtxM { PFN_SwtM swt; bool dark; };
static BOOL CALLBACK s_ThemeChildM(HWND child, LPARAM lp) {
    auto& c = *reinterpret_cast<ThemeEnumCtxM*>(lp);
    wchar_t cls[64] = {};
    ::GetClassNameW(child, cls, _countof(cls));
    const wchar_t* theme = nullptr;
    if (_wcsicmp(cls, L"Button") == 0) {
        LONG_PTR style = ::GetWindowLongPtr(child, GWL_STYLE);
        BYTE type = (BYTE)(style & 0x0FL);
        if (type == BS_GROUPBOX) { c.swt(child, nullptr, nullptr); return TRUE; }
        theme = c.dark ? L"DarkMode_Explorer" : nullptr;
    }
    else if (_wcsicmp(cls, L"Edit")          == 0) theme = c.dark ? L"DarkMode_CFD"      : nullptr;
    else if (_wcsicmp(cls, L"ComboBox")      == 0) theme = c.dark ? L"DarkMode_CFD"      : nullptr;
    else if (_wcsicmp(cls, L"SysListView32") == 0) theme = c.dark ? L"DarkMode_Explorer" : nullptr;
    else if (_wcsicmp(cls, L"SysHeader32")   == 0) theme = c.dark ? L"DarkMode_Explorer" : nullptr;
    else if (_wcsicmp(cls, L"Static")        == 0) theme = c.dark ? L"DarkMode_Explorer" : nullptr;
    c.swt(child, theme, nullptr);
    return TRUE;
}

LRESULT COdinMDlg::OnDeferredDarkMode(UINT, WPARAM, LPARAM, BOOL&) {
    ApplyDarkMode(m_hWnd);
    return 0;
}

void COdinMDlg::ApplyDarkMode(HWND hwnd) {

    static thread_local bool s_inThemePass = false;
    if (s_inThemePass)
      return;
    s_inThemePass = true;
  
    // ── title bar (DWM) ──────────────────────────────────────────────────
    typedef HRESULT (WINAPI *PFN_Dwm)(HWND, DWORD, LPCVOID, DWORD);
    static PFN_Dwm pfnDwm = nullptr;
    if (!pfnDwm) {
        HMODULE h = ::GetModuleHandleW(L"dwmapi.dll");
        if (h) pfnDwm = (PFN_Dwm)::GetProcAddress(h, "DwmSetWindowAttribute");
    }
    BOOL dark = IsDarkModeEnabled() ? TRUE : FALSE;
    if (pfnDwm) {
        if (FAILED(pfnDwm(hwnd, 20, &dark, sizeof(dark))))
            pfnDwm(hwnd, 19, &dark, sizeof(dark));
    }

    // ── client area (AllowDarkModeForWindow, uxtheme ordinal 133) ────────
    static PFN_AllowDarkM pfnAllow = nullptr;
    if (!pfnAllow) {
        HMODULE h = ::GetModuleHandleW(L"uxtheme.dll");
        if (!h) h = ::LoadLibraryW(L"uxtheme.dll");
        if (h) pfnAllow = (PFN_AllowDarkM)::GetProcAddress(h, MAKEINTRESOURCEA(133));
    }
    if (pfnAllow) {
        pfnAllow(hwnd, dark);
        DarkEnumCtxM ctx{ pfnAllow, dark };
        ::EnumChildWindows(hwnd, s_AllowDarkChildM, reinterpret_cast<LPARAM>(&ctx));
    }

    // Recreate dark brushes to match current mode
    if (m_darkBgBrush)   { ::DeleteObject(m_darkBgBrush);   m_darkBgBrush   = nullptr; }
    if (m_darkEditBrush) { ::DeleteObject(m_darkEditBrush); m_darkEditBrush = nullptr; }
    if (dark) {
        m_darkBgBrush   = ::CreateSolidBrush(RGB(32, 32, 32));
        m_darkEditBrush = ::CreateSolidBrush(RGB(45, 45, 48));
    }

    ApplyThemeToControls();
    ::RedrawWindow(hwnd, nullptr, nullptr,
                   RDW_INVALIDATE | RDW_ERASE | RDW_ALLCHILDREN | RDW_UPDATENOW);

    s_inThemePass = false;
}

void COdinMDlg::ApplyThemeToControls() {
    static PFN_SwtM pfnSwt = nullptr;
    if (!pfnSwt) {
        HMODULE h = ::GetModuleHandleW(L"uxtheme.dll");
        if (h) pfnSwt = (PFN_SwtM)::GetProcAddress(h, "SetWindowTheme");
    }
    if (!pfnSwt) return;

    bool dark = IsDarkModeEnabled();

    // Apply the correct uxtheme subapp string to every child control based on its class.
    // This covers buttons, checkboxes, statics, edits, combos, listview, and its header.
    ThemeEnumCtxM ctx{ pfnSwt, dark };
    ::EnumChildWindows(m_hWnd, s_ThemeChildM, reinterpret_cast<LPARAM>(&ctx));

    // ListView also needs explicit color assignments beyond the theme string.
    HWND hList = GetDlgItem(IDC_LIST_DRIVES);
    ListView_SetBkColor(hList,     dark ? RGB(32, 32, 32)   : CLR_DEFAULT);
    ListView_SetTextBkColor(hList, dark ? RGB(32, 32, 32)   : CLR_DEFAULT);
    ListView_SetTextColor(hList,   dark ? RGB(212, 212, 212) : CLR_DEFAULT);
    ::InvalidateRect(hList, nullptr, TRUE);
}

LRESULT COdinMDlg::OnCtlColor(UINT uMsg, WPARAM wParam, LPARAM /*lParam*/, BOOL& bHandled) {
    if (!IsDarkModeEnabled() || !m_darkBgBrush) { bHandled = FALSE; return 0; }
    HDC hdc = reinterpret_cast<HDC>(wParam);
    if (uMsg == WM_CTLCOLOREDIT || uMsg == WM_CTLCOLORLISTBOX) {
        ::SetTextColor(hdc, RGB(212, 212, 212));
        ::SetBkColor(hdc,   RGB(45,  45,  48));
        bHandled = TRUE;
        return reinterpret_cast<LRESULT>(m_darkEditBrush);
    }
    ::SetTextColor(hdc, RGB(212, 212, 212));
    ::SetBkMode(hdc, TRANSPARENT);  // group box labels need transparent bg mode
    ::SetBkColor(hdc,   RGB(32,  32,  32));
    bHandled = TRUE;
    return reinterpret_cast<LRESULT>(m_darkBgBrush);
}

LRESULT COdinMDlg::OnEraseBkgnd(UINT /*uMsg*/, WPARAM wParam, LPARAM /*lParam*/, BOOL& bHandled) {
    if (!IsDarkModeEnabled() || !m_darkBgBrush) { bHandled = FALSE; return 1; }
    RECT rc;
    GetClientRect(&rc);
    ::FillRect(reinterpret_cast<HDC>(wParam), &rc, m_darkBgBrush);
    return 1;
}

LRESULT COdinMDlg::OnSettingChange(UINT, WPARAM, LPARAM lParam, BOOL&) {
    if (lParam && wcscmp((LPCWSTR)lParam, L"ImmersiveColorSet") == 0)
    PostMessage(WM_APP + 100);
    return 0;
}

// ── ListView NM_CUSTOMDRAW ───────────────────────────────────────────────────

COLORREF COdinMDlg::StatusColor(CloneStatus s) {
    switch (s) {
    case CloneStatus::Cloning:    return RGB(0,   120, 215);  // blue
    case CloneStatus::Verifying:  return RGB(200, 140,   0);  // amber
    case CloneStatus::Complete:   return RGB(0,   150,   0);  // green
    case CloneStatus::Failed:     return RGB(200,   0,   0);  // red
    case CloneStatus::Empty:
    case CloneStatus::Stopped:    return RGB(128, 128, 128);  // gray
    default:                      return CLR_DEFAULT;
    }
}

void COdinMDlg::DrawProgressCell(HDC hdc, RECT rc, int progress) {
    // Background: system window color (adapts to dark/light mode)
    FillRect(hdc, &rc, ::GetSysColorBrush(COLOR_WINDOW));
    // Blue filled portion
    RECT fill = rc;
    fill.right = rc.left + (rc.right - rc.left) * progress / 100;
    HBRUSH blueBrush = CreateSolidBrush(RGB(0, 120, 215));
    FillRect(hdc, &fill, blueBrush);
    DeleteObject(blueBrush);
    // Percentage text centered (white over fill, system text color over bg)
    wchar_t buf[8];
    swprintf_s(buf, L"%d%%", progress);
    SetBkMode(hdc, TRANSPARENT);
    SetTextColor(hdc, progress > 50 ? RGB(255, 255, 255) : ::GetSysColor(COLOR_WINDOWTEXT));
    DrawTextW(hdc, buf, -1, &rc, DT_CENTER | DT_VCENTER | DT_SINGLELINE);
}

LRESULT COdinMDlg::OnListCustomDraw(int, LPNMHDR pnmh, BOOL& handled) {
    LPNMLVCUSTOMDRAW pcd = reinterpret_cast<LPNMLVCUSTOMDRAW>(pnmh);
    handled = FALSE;

    switch (pcd->nmcd.dwDrawStage) {
    case CDDS_PREPAINT:
        return CDRF_NOTIFYITEMDRAW;

    case CDDS_ITEMPREPAINT:
        return CDRF_NOTIFYSUBITEMDRAW;

    case CDDS_ITEMPREPAINT | CDDS_SUBITEM: {
        int item    = static_cast<int>(pcd->nmcd.dwItemSpec);
        int subItem = pcd->iSubItem;
        if (item < 0 || item >= static_cast<int>(m_driveSlots.size()))
            return CDRF_DODEFAULT;

        CloneStatus status = m_driveSlots[item]->GetStatus();

        if (subItem == COL_STATUS) {
            COLORREF c = StatusColor(status);
            if (c != CLR_DEFAULT) pcd->clrText = c;
            return CDRF_NEWFONT;
        }
        if (subItem == COL_PROGRESS && status == CloneStatus::Cloning) {
            DrawProgressCell(pcd->nmcd.hdc, pcd->nmcd.rc, m_driveSlots[item]->GetProgress());
            return CDRF_SKIPDEFAULT;
        }
        if (status == CloneStatus::Empty) {
            pcd->clrText = RGB(160, 160, 160);
            return CDRF_NEWFONT;
        }
        return CDRF_DODEFAULT;
    }
    default:
        return CDRF_DODEFAULT;
    }
}
