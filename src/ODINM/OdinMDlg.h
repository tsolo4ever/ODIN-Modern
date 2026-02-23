/******************************************************************************

    OdinM - Multi-Drive Clone Tool
    OdinMDlg.h - Main dialog class

******************************************************************************/

#pragma once
#include "stdafx.h"
#include "resource.h"
#include "DriveSlot.h"
#include <vector>

// Hash configuration structure
struct HashConfig {
    int partitionNumber = 1;
    std::wstring partitionType;
    
    bool sha1Enabled = true;
    bool sha256Enabled = false;
    
    std::wstring sha1Expected;
    std::wstring sha256Expected;
    
    bool failOnSha1Mismatch = true;
    bool failOnSha256Mismatch = false;
    
    std::wstring imagePath;
};

class COdinMDlg : public CDialogImpl<COdinMDlg>
{
public:
    enum { IDD = IDD_ODINM_MAIN };
    
    COdinMDlg();
    ~COdinMDlg();

    BEGIN_MSG_MAP(COdinMDlg)
        MESSAGE_HANDLER(WM_INITDIALOG, OnInitDialog)
        MESSAGE_HANDLER(WM_DESTROY, OnDestroy)
        MESSAGE_HANDLER(WM_DEVICECHANGE, OnDeviceChange)
        MESSAGE_HANDLER(WM_TIMER, OnTimer)
        COMMAND_ID_HANDLER(IDOK, OnOK)
        COMMAND_ID_HANDLER(IDCANCEL, OnCancel)
        COMMAND_ID_HANDLER(IDC_BUTTON_BROWSE, OnBrowse)
        COMMAND_ID_HANDLER(IDC_BUTTON_IMAGE_INFO, OnImageInfo)
        COMMAND_ID_HANDLER(IDC_BUTTON_CONFIG_HASH, OnConfigureHash)
        COMMAND_ID_HANDLER(IDC_BUTTON_START_ALL, OnStartAll)
        COMMAND_ID_HANDLER(IDC_BUTTON_STOP_ALL, OnStopAll)
        COMMAND_ID_HANDLER(IDC_BUTTON_CLEAR_LOG, OnClearLog)
        COMMAND_ID_HANDLER(IDC_BUTTON_EXPORT, OnExport)
        COMMAND_HANDLER(IDC_CHECK_AUTO_CLONE, BN_CLICKED, OnAutoCloneChanged)
    END_MSG_MAP()

private:
    // UI Controls
    CListViewCtrl m_driveList;
    CEdit m_imagePathEdit;
    CEdit m_logEdit;
    CButton m_autoCloneCheck;
    CButton m_verifyHashCheck;
    CButton m_stopOnFailCheck;
    CComboBox m_maxConcurrentCombo;
    CEdit m_targetSizeEdit;
    CStatic m_statusStatic;
    
    // Data
    std::vector<std::unique_ptr<CDriveSlot>> m_driveSlots;
    std::wstring m_imagePath;
    HashConfig m_hashConfig;
    bool m_autoCloneEnabled;
    int m_maxConcurrent;
    UINT_PTR m_timer;
    
    // Initialization
    LRESULT OnInitDialog(UINT msg, WPARAM wParam, LPARAM lParam, BOOL& handled);
    void InitializeDriveList();
    void InitializeControls();
    void LoadSettings();
    void SaveSettings();
    
    // Message handlers
    LRESULT OnDestroy(UINT msg, WPARAM wParam, LPARAM lParam, BOOL& handled);
    LRESULT OnDeviceChange(UINT msg, WPARAM wParam, LPARAM lParam, BOOL& handled);
    LRESULT OnTimer(UINT msg, WPARAM wParam, LPARAM lParam, BOOL& handled);
    LRESULT OnOK(WORD code, WORD id, HWND hwnd, BOOL& handled);
    LRESULT OnCancel(WORD code, WORD id, HWND hwnd, BOOL& handled);
    LRESULT OnBrowse(WORD code, WORD id, HWND hwnd, BOOL& handled);
    LRESULT OnImageInfo(WORD code, WORD id, HWND hwnd, BOOL& handled);
    LRESULT OnConfigureHash(WORD code, WORD id, HWND hwnd, BOOL& handled);
    LRESULT OnStartAll(WORD code, WORD id, HWND hwnd, BOOL& handled);
    LRESULT OnStopAll(WORD code, WORD id, HWND hwnd, BOOL& handled);
    LRESULT OnClearLog(WORD code, WORD id, HWND hwnd, BOOL& handled);
    LRESULT OnExport(WORD code, WORD id, HWND hwnd, BOOL& handled);
    LRESULT OnAutoCloneChanged(WORD code, WORD id, HWND hwnd, BOOL& handled);
    
    // Drive management
    void RefreshDrives();
    void DetectNewDrives();
    void StartClone(int slotIndex);
    void StopClone(int slotIndex);
    void UpdateDriveList();
    void UpdateStatus();
    
    // Logging
    void Log(const std::wstring& message);
    void LogDrive(int slotIndex, const std::wstring& message);
    
    // Hash operations
    bool LoadHashConfig(const std::wstring& imagePath);
    bool SaveHashConfig(const std::wstring& imagePath);
    void VerifyDrive(int slotIndex);
    
    // Helper methods
    std::wstring GetTimeString();
    bool IsValidImageFile(const std::wstring& path);
    ULONGLONG GetDriveSize(const std::wstring& driveLetter);
    std::wstring GetDriveName(const std::wstring& driveLetter);
};
