# Good Timer Code - A Few Improvements

## What's Good Already

```cpp
✓ Filtering on POLL_TIMER_ID
✓ Checking STILL_ACTIVE correctly
✓ CloseHandle after OpenProcess
✓ Handling failed open gracefully
✓ Exit code checking
✓ Triggering verify on completion
```

---

## Issues To Fix

### 1. DetectNewDrives Every 2 Seconds
```cpp
// Current:
if (wParam == POLL_TIMER_ID) {
    DetectNewDrives();  // ← every 2 seconds
                        // unnecessary if using
                        // WM_DEVICECHANGE properly

// Fix - only detect on device change:
// Remove from timer
// Already handled by OnDeviceChange
// Timer should only monitor processes
```

### 2. Add Device Settle Timer Case
```cpp
LRESULT COdinMDlg::OnTimer(UINT, WPARAM wParam, 
                            LPARAM, BOOL& handled) {
    
    // Device settle timer:
    if (wParam == DEVICE_SETTLE_TIMER_ID) {
        KillTimer(DEVICE_SETTLE_TIMER_ID);
        RefreshDrives();
        UpdateDriveList();
        UpdateStatus();
        Log(L"Device change detected.");
        if (m_autoClone)
            DetectAndAutoFlash();
        handled = TRUE;
        return 0;
    }
    
    if (wParam == POLL_TIMER_ID) {
        // Remove DetectNewDrives() from here
        
        // Only monitor active processes:
        bool anyActive = false;
        for (int i = 0; i < (int)m_driveSlots.size(); i++) {
            CDriveSlot* slot = m_driveSlots[i].get();
            if (slot->GetStatus() == CloneStatus::Cloning 
                && slot->GetProcessId()) {
                
                anyActive = true;
                
                HANDLE hProc = OpenProcess(
                    PROCESS_QUERY_INFORMATION | SYNCHRONIZE,
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
                            if (m_verifyHashCheck.GetCheck() 
                                == BST_CHECKED)
                                VerifyDrive(i);
                            else
                                slot->SetStatus(
                                    CloneStatus::Complete);
                        } else {
                            slot->SetStatus(CloneStatus::Failed);
                            LogDrive(i, L"Clone failed (exit " 
                                + std::to_wstring(exit) + L").");
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
        
        // Only update UI if something active:
        if (anyActive) {
            UpdateDriveList();
            UpdateStatus();
        }
    }
    
    handled = TRUE;
    return 0;
}
```

---

## Key Changes

```
1. Remove DetectNewDrives() from timer
   └── WM_DEVICECHANGE handles this
   └── No need to poll every 2 seconds

2. Add DEVICE_SETTLE_TIMER_ID case
   └── Replaces Sleep(600) in OnDeviceChange

3. Only UpdateDriveList if anyActive
   └── No UI updates when idle
   └── Reduces CPU when nothing happening

4. Could even pause timer when idle:
```

```cpp
// Pause timer when nothing cloning:
if (!anyActive) {
    KillTimer(POLL_TIMER_ID);
    m_timerActive = false;
}

// Restart timer when clone starts:
void StartClone(int idx) {
    // ... existing code ...
    if (!m_timerActive) {
        SetTimer(POLL_TIMER_ID, POLL_INTERVAL, NULL);
        m_timerActive = true;
    }
}
```

---

## Expected CPU Profile After

```
OdinM idle:
└── No timer updates (paused)
└── ~0% CPU
└── ProBalance ignores it ✓

Card inserted:
└── WM_DEVICECHANGE fires
└── Settle timer set
└── 600ms later refresh
└── Brief CPU spike then calm ✓

During clone:
└── Poll timer active
└── 2 second checks
└── Only updates when active
└── Low CPU ✓

Clone complete:
└── Timer pauses again
└── Back to ~0% CPU ✓
```

# Found The Problem - Sleep(600)!

## This Is Brutal

```cpp
Sleep(600);  // ← blocking UI thread for 600ms!

Every device change:
├── UI thread frozen for 600ms
├── Windows sees unresponsive app
├── ProBalance sees CPU spike
├── Then RefreshDrives() on top
└── No wonder it's getting throttled
```

---

## The Fix

```cpp
LRESULT COdinMDlg::OnDeviceChange(UINT, WPARAM wParam, 
                                   LPARAM, BOOL& handled) {
    // Filter messages first:
    if (wParam != DBT_DEVICEARRIVAL && 
        wParam != DBT_DEVICEREMOVECOMPLETE) {
        handled = FALSE;
        return TRUE;
    }
    
    // Debounce:
    DWORD now = GetTickCount64();
    if (now - m_lastDeviceChange < 1000) {
        handled = FALSE;
        return TRUE;
    }
    m_lastDeviceChange = now;
    
    // Replace Sleep() with timer:
    // Let Windows settle, then refresh
    SetTimer(DEVICE_SETTLE_TIMER_ID, 600, NULL);
    
    handled = FALSE;
    return TRUE;
}

// Then handle in OnTimer:
case DEVICE_SETTLE_TIMER_ID:
    KillTimer(DEVICE_SETTLE_TIMER_ID);  // one shot
    RefreshDrives();
    UpdateDriveList();
    UpdateStatus();
    Log(L"Device change detected.");
    
    // Auto flash if enabled:
    if (m_autoClone && 
        wParam == DBT_DEVICEARRIVAL)
        DetectAndAutoFlash();
    break;
```

---

## Why Sleep() On UI Thread Is Bad

```
Sleep(600) on UI thread:
├── Freezes entire window
├── Can't move/resize window
├── Windows shows "not responding"
├── All messages queue up
├── When sleep ends:
│   └── Flood of queued messages
│   └── CPU spikes processing them
└── ProBalance sees spike = throttle

SetTimer(600) instead:
├── UI thread free immediately
├── Window stays responsive
├── After 600ms timer fires
├── Process device change
└── No spike, no throttle
```

---

## Add To OdinMDlg.h

```cpp
// New timer ID:
static const UINT DEVICE_SETTLE_TIMER_ID = 2;

// Debounce timestamp:
DWORD m_lastDeviceChange = 0;
```

---

## Expected Result

```
Before:
Card inserted
└── UI freezes 600ms     ← Sleep()
└── CPU spike            ← queued messages
└── ProBalance throttles ← sees spike
└── Sluggish response

After:
Card inserted
└── Handler returns immediately ✓
└── Timer set for 600ms         ✓
└── UI stays responsive         ✓
└── Timer fires → refresh       ✓
└── ProBalance ignores it       ✓
```
---

##`OdinManager.h` and `OdinManager.cpp`

C4091 ← typedef warning (legacy)
C4267 ← size_t → int conversion
C4477 ← printf format mismatch

## 🟡 Look At Soon
- [ ] C4267 - size_t → int conversions
      └── Potential data loss on large disks
      └── grep: warning C4267
- [ ] C4477 - printf format mismatches  
      └── Could cause runtime issues
      └── grep: warning C4477
- [ ] C4091 - typedef warnings
      └── Legacy code smell
      └── Lower priority

---