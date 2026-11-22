"""Synthetic confirmation leases for UI regressions; never open real volumes."""

from types import SimpleNamespace


class FakeLease:
    def __init__(self, drive):
        self.disk = SimpleNamespace(disk_number=drive.disk_number)
        self.active = True

    def close(self):
        self.active = False

    def validate(self, disk):
        if not self.active or disk.disk_number != self.disk.disk_number:
            raise RuntimeError("fake isolation mismatch")


class FakeConfirmationManager:
    busy = False

    def __init__(self, _root):
        self.leases = {}

    def get(self, idx):
        return self.leases.get(idx)

    def request(self, idx, drive, _image, ready, _failed):
        self.release(idx)
        lease = FakeLease(drive)
        self.leases[idx] = lease
        ready(lease)

    def release(self, idx):
        lease = self.leases.pop(idx, None)
        if lease:
            lease.close()

    def close(self):
        for idx in list(self.leases):
            self.release(idx)
