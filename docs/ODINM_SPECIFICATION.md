# OdinM - Multi-Drive Clone Tool Specification

## Overview
OdinM is a multi-drive cloning tool that can flash up to 5 drives simultaneously with hash verification support for data integrity.

## Drive Structure
```
[Unallocated] | [Partition 1: FAT32] | [Partition 2: ext4] | [Partition 3: ext4] | [Unallocated]
```

## Supported Hash Algorithms
1. **SHA-1** - 160-bit cryptographic hash (40 hex characters)
2. **SHA-256** - 256-bit cryptographic hash (64 hex characters)

**Note:** Proprietary hash algorithms (CDCK, Kobe4) removed - using standard SHA hashes only.

---

## Main Window UI

```
┌─────────────────────────────────────────────────────────────────────────┐
│ OdinM - Multi-Drive Clone Tool                                 [_][□][X]│
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  Source Image: [C:\images\sentinel.img           ] [Browse...] [Info]   │
│                                                                         │
│  ┌───────────────────────────────────────────────────────────────────┐  │
│  │ Clone Operations                                                 │  │
│  ├───┬──────────────┬───────────┬──────────┬──────────┬────────────     │
│  │ # │ Drive        │ Status    │ Progress │ Verify   │ CDCK       │    │
│  ├───┼──────────────┼───────────┼──────────┼──────────┼────────────┤    │
│  │ 1 │ F:\ (8.0 GB) │ Complete  │ 100%     │ ✓ PASSED │ DD35       │    │
│  │ 2 │ G:\ (8.0 GB) │ Verifying │ 100%     │ Checking │ ...        │    │
│  │ 3 │ H:\ (8.0 GB) │ Complete  │ 100%     │ ✓ PASSED │ B014       │    │
│  │ 4 │ I:\ (8.0 GB) │ Cloning   │ ████░░░  │ -        │ -          │    │
│  │ 5 │ (empty)      │ -         │ ━━━━━━━  │ -        │ -          │    │
│  └───┴──────────────┴───────────┴──────────┴──────────┴────────────┘    │
│                                                                         │
│  ┌─ Settings ────────────────────────────────────────────────────────┐  │
│  │ ☑ Auto-clone on device insertion                                 │   │
│  │ ☑ Verify hash after clone                   [Configure Hashes]   │   │
│  │ ☐ Stop all on verification failure                               │   │
│  │ Max concurrent: [3▼] (1-5)   Target size: [8] GB (±10%)           │  │
│  └───────────────────────────────────────────────────────────────────┘  │
│                                                                         │
│  Activity Log:                                                          │
│  ┌───────────────────────────────────────────────────────────────────┐  │
│  │ 07:45:12 - Drive F:\ clone completed                              │  │
│  │ 07:45:15 - Drive F:\ SHA-1 verification: PASSED                   │  │
│  │ 07:45:16 - Drive F:\ CDCK: DD35 (matches expected)                │  │
│  │ 07:45:30 - Drive G:\ clone started                                │  │
│  └───────────────────────────────────────────────────────────────────┘  │
│                                                                         │
│              [Start All]  [Stop All]  [Clear Log]  [Export Results]     │
│                                                                         │
│  Status: 2 verified (passed), 1 active, 0 failed, 2 slots available     │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Hash Configuration Dialog (Popup)

Accessed via [Configure Hashes] button or automatically when new image is loaded for first time.

```
┌─────────────────────────────────────────────────────────────────────┐
│ Configure Hash Verification                                   [?][X]│
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  Image: sentinel.img                                                │
│  Configure expected hash values for verification after cloning.     │
│                                                                     │
│  ┌─ Partition Selection ─────────────────────────────────────────┐ │
│  │ Verify: [● Partition 1 (FAT32)                              ▼]│ │
│  │         ○ Partition 2 (ext4)                                  │ │
│  │         ○ Partition 3 (ext4)                                  │ │
│  │         ○ Entire disk                                         │ │
│  └───────────────────────────────────────────────────────────────┘ │
│                                                                     │
  │  ┌─ Expected Hash Values ────────────────────────────────────────┐ │
  │  │                                                                │ │
  │  │  SHA-1:      [                                              ] │ │
  │  │              (40 hex chars)             Status: [Not Set ]    │ │
  │  │              Example: FD218079E7D01CF746042EE08F05F7BD7DA2A8E2│ │
  │  │                                                                │ │
  │  │  SHA-256:    [                                              ] │ │
  │  │              (64 hex chars)             Status: [Not Set ]    │ │
  │  │              Example: 5a2c8f9e3d7b1a4c6e2f8d9c2b4a6e1f...    │ │
  │  │                                                                │ │
  │  └────────────────────────────────────────────────────────────────┘ │
  │                                                                     │
  │  ┌─ Calculate Current Hashes ────────────────────────────────────┐ │
  │  │ Calculate hash values from currently loaded image:             │ │
  │  │ [Calculate All]  [Calculate SHA-1]  [Calculate SHA-256]       │ │
  │  │                                                                │ │
  │  │ Calculated values will be displayed above for copying.         │ │
  │  └────────────────────────────────────────────────────────────────┘ │
  │                                                                     │
  │  ☑ Enable SHA-1          ☑ Fail on SHA-1 mismatch                 │
  │  ☑ Enable SHA-256        ☐ Fail on SHA-256 mismatch               │
│                                                                     │
│                        [Save]  [Cancel]  [Load from File]          │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Hash Verification Process

### Workflow:

1. **Load Image**
   - User selects .img file
   - If no hash configuration exists → show Hash Configuration Dialog
   - Scan partition table to detect structure

2. **Configure Hashes** (One-time setup per image)
   - Select partition to verify (default: Partition 1)
   - Enter expected hash values (Kobe4, CDCK, SHA-1, SHA-256)
   - OR calculate hashes from source image
   - Choose which hashes to verify
   - Save configuration to `imagename.hashes` file

3. **Clone Operation**
   - ODINC clones entire drive
   - Progress shown in UI

4. **Post-Clone Verification**
   - Read selected partition from cloned drive
   - Calculate enabled hashes
   - Compare with expected values
   - Display results in Verify and CDCK columns

5. **Results**
   - ✓ PASSED = All enabled hashes match
   - ✗ FAILED = One or more hashes don't match
   - Show CDCK value in separate column for quick visual check

---

## Hash Algorithms Implementation

### 1. CDCK (Checksum - 4 hex chars)
```cpp
// Calculate CRC32 and take last 4 hex chars
DWORD CalculateCDCK(BYTE* data, DWORD length) {
    DWORD crc32 = CalculateCRC32(data, length);
    return crc32 & 0xFFFF; // Last 16 bits = 4 hex chars
}
// Display as: DD35, B014, etc.
```

### 2. Kobe4 (4-character identifier)
```cpp
// This appears to be a custom identifier from your system
// Could be:
// - Device serial number last 4 chars
// - Custom manufacturing code
// - Batch identifier
// 
// Examples from PDF: 1203, 6H85, 2511, U204, P2A8
```

### 3. SHA-1 (40 hex chars)
```cpp
CALG_SHA1  // Windows CryptoAPI
// Example: FD218079E7D01CF746042EE08F05F7BD7DA2A8E2
```

### 4. SHA-256 (64 hex chars)
```cpp
CALG_SHA_256  // Windows CryptoAPI
// Example: 5a2c8f9e3d7b1a4c6e2f8d9c2b4a6e1f3c5d7b9a1e3f5c7d9b2a4e6f8c1d3b5a7
```

---

## Configuration File Format

### imagename.hashes (JSON)
```json
{
  "image": "sentinel.img",
  "partition": 1,
  "partition_type": "FAT32",
  "hashes": {
    "kobe4": {
      "enabled": true,
      "expected": "1203",
      "fail_on_mismatch": false
    },
    "cdck": {
      "enabled": true,
      "expected": "DD35",
      "fail_on_mismatch": false
    },
    "sha1": {
      "enabled": true,
      "expected": "FD218079E7D01CF746042EE08F05F7BD7DA2A8E2",
      "fail_on_mismatch": true
    },
    "sha256": {
      "enabled": false,
      "expected": "",
      "fail_on_mismatch": false
    }
  },
  "created": "2026-02-22T07:45:00Z",
  "last_verified": "2026-02-22T08:30:00Z"
}
```

---

## Partition Detection

```cpp
struct PartitionInfo {
    int number;              // 1, 2, 3
    ULONGLONG offset;        // Byte offset from start
    ULONGLONG size;          // Size in bytes
    string type;             // "FAT32", "ext4", "unallocated"
    string label;            // Volume label if available
};

// Detect partitions using Windows API
vector<PartitionInfo> DetectPartitions(const wchar_t* imagePath) {
    // Open image file
    // Read partition table (MBR or GPT)
    // Parse partition entries
    // Return list of partitions
}
```

---

## Hash Calculation Performance

**For 8GB Partition:**
- CDCK (CRC32): ~20-30 seconds
- SHA-1: ~30-45 seconds  
- SHA-256: ~40-60 seconds

**Optimization:**
- Calculate all enabled hashes in single pass
- Read data once, update all hash contexts
- Use 1MB buffer size for optimal performance

```cpp
void CalculateAllHashes(
    HANDLE hDrive,
    ULONGLONG offset,
    ULONGLONG size,
    PartitionHashes& results)
{
    const DWORD BUFFER_SIZE = 1024 * 1024; // 1MB
    BYTE buffer[BUFFER_SIZE];
    
    // Initialize all enabled hash contexts
    HCRYPTHASH hSHA1 = CreateHash(CALG_SHA1);
    HCRYPTHASH hSHA256 = CreateHash(CALG_SHA_256);
    DWORD crc32 = 0;
    
    // Single-pass read
    ULONGLONG remaining = size;
    while (remaining > 0) {
        DWORD toRead = min(BUFFER_SIZE, remaining);
        ReadFile(hDrive, buffer, toRead, &bytesRead, NULL);
        
        // Update all hashes simultaneously
        if (enabled.sha1)
            CryptHashData(hSHA1, buffer, bytesRead, 0);
        if (enabled.sha256)
            CryptHashData(hSHA256, buffer, bytesRead, 0);
        if (enabled.cdck)
            crc32 = UpdateCRC32(crc32, buffer, bytesRead);
        
        remaining -= bytesRead;
    }
    
    // Finalize and store results
    // ...
}
```

---

## Verification Results Display

### Per-Drive Status:
- **Verify Column:** Overall result (✓ PASSED / ✗ FAILED)
- **CDCK Column:** Quick visual check (DD35, B014, etc.)
- **Details:** Click drive row to see detailed results

### Detailed Results Dialog:
```
┌──────────────────────────────────────────────┐
│ Verification Details - Drive F:\        [X] │
├──────────────────────────────────────────────┤
│ Image: sentinel.img                          │
│ Partition 1 (FAT32) - 2.5 GB                │
│                                              │
│ Hash Type   Expected        Calculated      │
│ ─────────────────────────────────────────────│
│ Kobe4       1203            1203      ✓     │
│ CDCK        DD35            DD35      ✓     │
│ SHA-1       FD218079...     FD218079... ✓   │
│ SHA-256     (disabled)      -          -    │
│                                              │
│ Overall: PASSED                              │
│ Time: 45 seconds                             │
│                          [Copy Results] [OK] │
└──────────────────────────────────────────────┘
```

---

## Export Results

Export verification results to CSV or text file:

```csv
Timestamp,Drive,Image,Partition,Kobe4,Kobe4_Match,CDCK,CDCK_Match,SHA1_Match,SHA256_Match,Overall
2026-02-22 08:30:15,F:\,sentinel.img,1,1203,PASS,DD35,PASS,PASS,-,PASS
2026-02-22 08:31:42,G:\,sentinel.img,1,1203,PASS,DD35,PASS,PASS,-,PASS
2026-02-22 08:33:08,H:\,sentinel.img,1,1203,PASS,DD35,PASS,PASS,-,PASS
```

---

## Implementation Phases

### Phase 1: Core Functionality (4-6 hours)
- [ ] Create OdinM WTL dialog application
- [ ] Implement drive list view with 5 slots
- [ ] Add image file selection
- [ ] Spawn ODINC processes for cloning
- [ ] Track process status and completion

### Phase 2: Hash Configuration (3-4 hours)
- [ ] Create Hash Configuration dialog
- [ ] Implement partition detection
- [ ] Add hash input fields (Kobe4, CDCK, SHA-1, SHA-256)
- [ ] Save/load hash configuration files
- [ ] Add "Calculate from image" feature

### Phase 3: Hash Verification (4-5 hours)
- [ ] Implement CRC32/CDCK calculation
- [ ] Implement SHA-1 calculation
- [ ] Implement SHA-256 calculation
- [ ] Read partition data from cloned drives
- [ ] Compare hashes and display results
- [ ] Add CDCK column to drive list

### Phase 4: Polish & Features (2-3 hours)
- [ ] Add detailed results dialog
- [ ] Implement export to CSV
- [ ] Add activity logging
- [ ] Persistent settings (OdinM.ini)
- [ ] Error handling and retry logic
- [ ] Testing with real drives

---

## Total Estimated Time: 13-18 hours (2 days)

---

## Dependencies

- **ODIN/ODINC**: For actual drive cloning
- **Windows CryptoAPI**: For SHA-1/SHA-256
- **WTL 10.0**: For GUI framework
- **JSON library**: For hash configuration files (or use simple INI format)

---

## Notes

- **Kobe4**: Need clarification on what this represents. Could be:
  - Device manufacturing code
  - Firmware version identifier
  - Batch number
  - Custom identifier from your production system
  
- **Partition Types**: ext4 partitions will appear as "Unknown" or "RAW" in Windows. Hash verification will still work since we're reading raw sectors.

- **Unallocated Space**: Not hashed (only actual partition data is verified)

---

*Created: 2026-02-22*
*Version: 1.0*
