import sys
sys.path.insert(0, r"d:\github\ODIN-Modern\OdinM_py\scripts")
import pyimager


class FakeDisk:
    _fail_until = {}  # letter -> attempts remaining before success

    def __init__(self, path, write=False):
        self.path = path
        self.closed = False
        self.unlocked = False
        self._lock_calls = 0

    def lock(self):
        self._lock_calls += 1
        letter = self.path.rstrip(":").rstrip("\\").split("\\")[-1]
        remaining = FakeDisk._fail_until.get(letter, 0)
        if remaining > 0:
            FakeDisk._fail_until[letter] = remaining - 1
            return False
        return True

    def dismount(self):
        return True

    def unlock(self):
        self.unlocked = True

    def close(self):
        self.closed = True


pyimager.Win32Disk = FakeDisk

logs = []


def on_log(m):
    logs.append(m)


# Case 1: E: fails twice then succeeds on the 3rd attempt (within retries=5)
FakeDisk._fail_until = {"E": 2}
locked = pyimager.lock_and_dismount_volumes(["E"], retries=5, delay_s=0.01, on_log=on_log)
print("case1 OK - locked:", [d.path for d in locked], "lock_calls:", locked[0]._lock_calls)
for d in locked:
    d.unlock()
    d.close()

# Case 2: F: fails every single time (retries exhausted) -> should raise OSError
FakeDisk._fail_until = {"F": 999}
try:
    pyimager.lock_and_dismount_volumes(["F"], retries=3, delay_s=0.01)
    print("case2 FAIL: did not raise")
except OSError as e:
    print("case2 OK - raised OSError:", e)

# Case 3: E: OK, then G: exhausts retries -> E: must be unlocked+closed on cleanup
FakeDisk._fail_until = {"G": 999}
try:
    pyimager.lock_and_dismount_volumes(["E", "G"], retries=2, delay_s=0.01)
    print("case3 FAIL: did not raise")
except OSError as e:
    print("case3 OK - raised OSError:", e)

print("logs from case1:", logs)
