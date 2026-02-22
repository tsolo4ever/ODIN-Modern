/******************************************************************************

    OdinM - Multi-Drive Clone Tool
    HashConfigDlg.h - Dialog for configuring expected hash values

******************************************************************************/

#pragma once
#include "stdafx.h"
#include "resource.h"
#include "OdinMDlg.h"
#include "HashCalculator.h"

class CHashConfigDlg : public CDialogImpl<CHashConfigDlg>
{
public:
    enum { IDD = IDD_HASH_CONFIG };

    // Pass config by reference; caller owns the struct
    CHashConfigDlg(HashConfig& config);

    BEGIN_MSG_MAP(CHashConfigDlg)
        MESSAGE_HANDLER(WM_INITDIALOG, OnInitDialog)
        COMMAND_ID_HANDLER(IDOK,                   OnOK)
        COMMAND_ID_HANDLER(IDCANCEL,               OnCancel)
        COMMAND_ID_HANDLER(IDC_BUTTON_CALC_ALL,    OnCalculateAll)
        COMMAND_ID_HANDLER(IDC_BUTTON_CALC_SHA1,   OnCalculateSHA1)
        COMMAND_ID_HANDLER(IDC_BUTTON_CALC_SHA256, OnCalculateSHA256)
        COMMAND_ID_HANDLER(IDC_BUTTON_LOAD_FILE,   OnLoadFile)
    END_MSG_MAP()

private:
    HashConfig& m_config;

    // Message handlers
    LRESULT OnInitDialog(UINT msg, WPARAM wParam, LPARAM lParam, BOOL& handled);
    LRESULT OnOK        (WORD code, WORD id, HWND hwnd, BOOL& handled);
    LRESULT OnCancel    (WORD code, WORD id, HWND hwnd, BOOL& handled);
    LRESULT OnCalculateAll   (WORD code, WORD id, HWND hwnd, BOOL& handled);
    LRESULT OnCalculateSHA1  (WORD code, WORD id, HWND hwnd, BOOL& handled);
    LRESULT OnCalculateSHA256(WORD code, WORD id, HWND hwnd, BOOL& handled);
    LRESULT OnLoadFile       (WORD code, WORD id, HWND hwnd, BOOL& handled);

    // Helpers
    void CalculateHashesFromImage(bool calcSHA1, bool calcSHA256);
    void LoadConfigToControls();
    void SaveControlsToConfig();

    std::wstring GetControlText(int controlId);
    bool         GetCheckState (int controlId);
    void         SetControlText(int controlId, const std::wstring& text);
    void         SetCheckState (int controlId, bool checked);
    void         UpdateStatusLabels();
};
