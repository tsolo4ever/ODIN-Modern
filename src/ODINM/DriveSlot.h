/******************************************************************************

    OdinM - Multi-Drive Clone Tool
    DriveSlot.h - Represents a single drive slot with cloning status

******************************************************************************/

#pragma once
#include "stdafx.h"

// Clone status enumeration
enum class CloneStatus {
    Empty,          // No drive detected
    Ready,          // Drive detected, ready to clone
    Cloning,        // Currently cloning
    Verifying,      // Clone complete, verifying hash
    Complete,       // Clone and verification complete
    Failed,         // Clone or verification failed
    Stopped         // User stopped operation
};

// Verification result
struct VerificationResult {
    bool sha1Pass = false;
    bool sha256Pass = false;
    bool overallPass = false;
    
    std::wstring sha1Value;
    std::wstring sha256Value;
    
    std::wstring errorMessage;
};

// DriveSlot class
class CDriveSlot {
public:
    CDriveSlot(int slotNumber);
    ~CDriveSlot();

    // Getters
    int GetSlotNumber() const { return m_slotNumber; }
    std::wstring GetDriveLetter() const { return m_driveLetter; }
    std::wstring GetDriveName() const { return m_driveName; }
    ULONGLONG GetDriveSize() const { return m_driveSize; }
    CloneStatus GetStatus() const { return m_status; }
    int GetProgress() const { return m_progress; }
    const VerificationResult& GetVerificationResult() const { return m_verifyResult; }
    DWORD GetProcessId() const { return m_processId; }
    
    // Setters
    void SetDrive(const std::wstring& letter, const std::wstring& name, ULONGLONG size);
    void ClearDrive();
    void SetStatus(CloneStatus status);
    void SetProgress(int progress);
    void SetVerificationResult(const VerificationResult& result);
    void SetProcessId(DWORD pid);
    
    // Status queries
    bool IsEmpty() const { return m_status == CloneStatus::Empty; }
    bool IsActive() const { 
        return m_status == CloneStatus::Cloning || m_status == CloneStatus::Verifying;
    }
    bool IsComplete() const { return m_status == CloneStatus::Complete; }
    bool IsFailed() const { return m_status == CloneStatus::Failed; }
    
    // Formatting helpers
    std::wstring GetStatusString() const;
    std::wstring GetDriveSizeString() const;
    std::wstring GetProgressString() const;
    
private:
    int m_slotNumber;
    std::wstring m_driveLetter;      // e.g., "F:"
    std::wstring m_driveName;        // e.g., "USB DISK"
    ULONGLONG m_driveSize;           // Size in bytes
    CloneStatus m_status;
    int m_progress;                  // 0-100
    VerificationResult m_verifyResult;
    DWORD m_processId;               // Process ID of ODINC.exe for this slot
};
