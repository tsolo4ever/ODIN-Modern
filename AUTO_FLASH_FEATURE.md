# Auto-Flash Feature for 8GB CF Cards

## Overview

The auto-flash feature automatically detects when an 8GB CF card is inserted and triggers a restore operation using the currently selected image file. This feature is designed for production environments where multiple CF cards need to be flashed with the same image.

## How It Works

1. **Enable the Feature**: Check the "Auto-flash 8GB CF cards" checkbox in the main ODIN dialog
2. **Select Image File**: Choose the image file you want to restore to CF cards
3. **Switch to Restore Mode**: Ensure the "Restore" radio button is selected
4. **Insert CF Card**: When an 8GB CF card is inserted, ODIN will:
   - Detect the card automatically (removable drive, ~8GB size ±10% tolerance)
   - Select it in the drive list
   - Automatically trigger the restore operation

## User Interface

### Main Dialog
- A new checkbox labeled "Auto-flash 8GB CF cards" has been added below the Backup/Restore radio buttons
- The checkbox state is persisted in the ODIN.ini configuration file

## Technical Details

### Detection Logic
- **Drive Type**: Must be a removable drive (driveRemovable)
- **Size Match**: Drive size must be within ±10% of 8GB (8,589,934,592 bytes)
- **Tolerance**: Accepts drives from 7.2GB to 8.8GB to account for manufacturer variations

### Configuration
- Setting is stored in ODIN.ini under `[MainWindow]` section as `AutoFlashEnabled`
- Setting persists between sessions

### Safety Features
- Only triggers when:
  - Auto-flash is enabled
  - Restore mode is selected
  - A valid image file is selected
  - An 8GB CF card is detected
- Uses existing restore validation and confirmation dialogs
- All standard ODIN safety checks apply

## Implementation Files Modified

### Resource Files
- **src/ODIN/resource.h**: Added `IDC_CHECK_AUTOFLASH` control ID (1053)
- **src/ODIN/ODIN.rc**: Added checkbox control to main dialog

### Header Files
- **src/ODIN/ODINDlg.h**: Added:
  - `fAutoFlashEnabled` configuration entry
  - `DetectCFCard()` method
  - `TriggerAutoFlash(int driveIndex)` method
  - `OnBnClickedCheckAutoflash()` message handler

### Implementation Files
- **src/ODIN/ODINDlg.cpp**: Implemented:
  - Checkbox initialization in `InitControls()`
  - CF card detection logic in `DetectCFCard()`
  - Auto-flash trigger in `TriggerAutoFlash()`
  - Device change monitoring in `OnDeviceChanged()`
  - Checkbox state handler in `OnBnClickedCheckAutoflash()`

## Usage Example

### Production Workflow
1. Launch ODIN
2. Select "Restore" mode
3. Browse and select your master image file (e.g., `master_image.img`)
4. Check "Auto-flash 8GB CF cards"
5. Insert CF cards one at a time
6. ODIN automatically restores the image to each card
7. Remove card when complete, insert next card

### Batch Processing
For flashing multiple cards:
- Keep ODIN open with auto-flash enabled
- Insert card → Automatic restore starts
- Wait for completion
- Remove card
- Insert next card → Process repeats

## Troubleshooting

### Card Not Detected
- Verify the card is approximately 8GB (within 7.2GB - 8.8GB range)
- Ensure the card is recognized by Windows as a removable drive
- Check that "Auto-flash" is enabled
- Verify "Restore" mode is selected
- Confirm an image file is selected

### Wrong Card Detected
- The feature detects any removable drive within the 8GB size range
- Be careful not to have other 8GB removable drives connected
- The 10% tolerance accounts for manufacturer size variations

### Safety
- All standard ODIN confirmations and safety checks still apply
- You can cancel the restore operation at any time
- The feature respects all ODIN settings (compression, split files, etc.)

## Future Enhancements

Potential improvements for future versions:
- Configurable size detection (not just 8GB)
- Optional confirmation dialog before auto-flash
- Auto-eject card when complete
- Support for label matching
- Settings dialog for advanced configuration
- Progress notification sounds
- Batch processing statistics

## Version Information

- **Feature Added**: Version 0.3.5 (2026-02-22)
- **Compatible With**: Windows XP SP2 and later
- **Build Requirements**: Visual Studio 2026, WTL 10.0

## License

This feature is part of ODIN (Open Disk Imager in a Nutshell) and is licensed under the GNU General Public License v3.
