/******************************************************************************

    OdinM - Multi-Drive Clone Tool
    DriveSlot.cpp - Implementation of drive slot management

******************************************************************************/

#include "stdafx.h"
#include "DriveSlot.h"

CDriveSlot::CDriveSlot(int slotNumber)
    : m_slotNumber(slotNumber)
    , m_driveSize(0)
    , m_status(CloneStatus::Empty)
    , m_progress(0)
    , m_processId(0)
{
}

CDriveSlot::~CDriveSlot()
{
}

void CDriveSlot::SetDrive(const std::wstring& letter, const std::wstring& name, ULONGLONG size)
{
    m_driveLetter = letter;
    m_driveName = name;
    m_driveSize = size;
    m_status = CloneStatus::Ready;
    m_progress = 0;
    m_verifyResult = VerificationResult();
}

void CDriveSlot::ClearDrive()
{
    m_driveLetter.clear();
    m_driveName.clear();
    m_driveSize = 0;
    m_status = CloneStatus::Empty;
    m_progress = 0;
    m_processId = 0;
    m_verifyResult = VerificationResult();
}

void CDriveSlot::SetStatus(CloneStatus status)
{
    m_status = status;
}

void CDriveSlot::SetProgress(int progress)
{
    m_progress = max(0, min(100, progress));
}

void CDriveSlot::SetVerificationResult(const VerificationResult& result)
{
    m_verifyResult = result;
}

void CDriveSlot::SetProcessId(DWORD pid)
{
    m_processId = pid;
}

std::wstring CDriveSlot::GetStatusString() const
{
    switch (m_status) {
        case CloneStatus::Empty:      return L"-";
        case CloneStatus::Ready:      return L"Ready";
        case CloneStatus::Cloning:    return L"Cloning";
        case CloneStatus::Verifying:  return L"Verifying";
        case CloneStatus::Complete:   return L"Complete";
        case CloneStatus::Failed:     return L"Failed";
        case CloneStatus::Stopped:    return L"Stopped";
        default:                      return L"Unknown";
    }
}

std::wstring CDriveSlot::GetDriveSizeString() const
{
    if (m_driveSize == 0) {
        return L"-";
    }
    
    double sizeGB = m_driveSize / (1024.0 * 1024.0 * 1024.0);
    wchar_t buffer[64];
    swprintf_s(buffer, L"%.1f GB", sizeGB);
    return buffer;
}

std::wstring CDriveSlot::GetProgressString() const
{
    if (m_status == CloneStatus::Empty || m_status == CloneStatus::Ready) {
        return L"-";
    }
    
    wchar_t buffer[32];
    swprintf_s(buffer, L"%d%%", m_progress);
    return buffer;
}
