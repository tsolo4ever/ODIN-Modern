"""Automount lifecycle tests; no commands or policy changes reach Windows."""

import subprocess
import sys
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import app as app_module  # noqa: E402
import automount_guard as guard_module  # noqa: E402
import config_manager  # noqa: E402
import main as entry  # noqa: E402
import runtime_interlock  # noqa: E402
from automount_guard import AutomountError, AutomountGuard  # noqa: E402


class Policy:
    def __init__(self, enabled=True):
        self.enabled = enabled
        self.events = []

    def set(self, enabled):
        self.events.append("/E" if enabled else "/N")
        self.enabled = enabled

    @contextmanager
    def own(self):
        self.events.append("claim")
        try:
            yield
        finally:
            self.events.append("release")

    def guard(self, **overrides):
        values = {
            "query_enabled": lambda: self.enabled,
            "set_enabled": self.set,
            "ownership": self.own,
        }
        values.update(overrides)
        return AutomountGuard(**values)


class AutomountTests(unittest.TestCase):
    def test_restore_on_normal_close_and_on_body_exception(self):
        for fail in (False, True):
            with self.subTest(fail=fail):
                policy = Policy()
                try:
                    with policy.guard():
                        self.assertFalse(policy.enabled)
                        if fail:
                            raise ValueError("UI failed")
                except ValueError:
                    self.assertTrue(fail)
                self.assertTrue(policy.enabled)
                self.assertEqual(policy.events, ["claim", "/N", "/E", "release"])

    def test_already_disabled_policy_is_preserved(self):
        policy = Policy(False)
        with policy.guard() as guard:
            self.assertFalse(guard.was_enabled)
        self.assertFalse(policy.enabled)
        self.assertEqual(policy.events, ["claim", "/N", "release"])

    def test_timeout_after_disable_restores_original_setting(self):
        policy = Policy()

        def setter(enabled):
            policy.set(enabled)
            if not enabled:
                raise AutomountError("timed out after command ran")

        with self.assertRaisesRegex(AutomountError, "timed out"):
            with policy.guard(set_enabled=setter):
                self.fail("startup must not continue")
        self.assertTrue(policy.enabled)
        self.assertEqual(policy.events, ["claim", "/N", "/E", "release"])

    def test_unreadable_policy_does_not_run_mountvol(self):
        policy = Policy()
        with self.assertRaisesRegex(AutomountError, "read failed"):
            with policy.guard(query_enabled=lambda: self.fail_query()):
                self.fail("startup must not continue")
        self.assertEqual(policy.events, ["claim", "release"])

    @staticmethod
    def fail_query():
        raise AutomountError("read failed")

    def test_unsuccessful_disable_blocks_startup(self):
        policy = Policy()
        with self.assertRaisesRegex(AutomountError, "remained enabled"):
            with policy.guard(set_enabled=lambda value: policy.events.append(value)):
                self.fail("startup must not continue")
        self.assertEqual(policy.events, ["claim", False, True, "release"])

    def test_restore_failure_reports_recovery_and_releases_ownership(self):
        policy = Policy()

        def setter(enabled):
            if enabled:
                raise OSError("denied")
            policy.set(enabled)

        with self.assertRaisesRegex(AutomountError, "mountvol /E"):
            with policy.guard(set_enabled=setter):
                pass
        self.assertEqual(policy.events[-1], "release")

    def test_windows_mutex_rejects_second_owner_without_policy_commands(self):
        policy = Policy()
        # This creates kernel handles only; policy operations remain injected.
        with guard_module._exclusive_ownership():
            with self.assertRaisesRegex(AutomountError, "Another Odin"):
                with policy.guard(ownership=guard_module._exclusive_ownership):
                    self.fail("a second instance must not change policy")
        self.assertEqual(policy.events, [])
        with guard_module._exclusive_ownership():
            pass

    def test_mountvol_uses_system_path_and_no_shell(self):
        for enabled, switch in ((False, "/N"), (True, "/E")):
            with patch.object(guard_module.subprocess, "run") as run:
                run.return_value = SimpleNamespace(returncode=0)
                guard_module.set_automount(enabled)
                args, kwargs = run.call_args
                self.assertEqual(Path(args[0][0]).name.lower(), "mountvol.exe")
                self.assertTrue(Path(args[0][0]).is_absolute())
                self.assertEqual(args[0][1], switch)
                self.assertFalse(kwargs.get("shell", False))
                self.assertEqual(kwargs["creationflags"], subprocess.CREATE_NO_WINDOW)
                self.assertEqual(kwargs["timeout"], 15)

    def test_mountvol_command_failures_are_not_silent(self):
        with patch.object(guard_module.subprocess, "run") as run:
            run.return_value = SimpleNamespace(returncode=5, stderr="denied", stdout="")
            with self.assertRaisesRegex(AutomountError, "exit 5.*denied"):
                guard_module.set_automount(False)
            run.side_effect = subprocess.TimeoutExpired("mountvol", 15)
            with self.assertRaisesRegex(AutomountError, "mountvol /N failed"):
                guard_module.set_automount(False)

    def test_registry_snapshot_handles_default_and_disabled_values(self):
        with (
            patch.object(guard_module.winreg, "OpenKey"),
            patch.object(guard_module.winreg, "QueryValueEx") as query,
        ):
            query.side_effect = FileNotFoundError()
            self.assertTrue(guard_module.automount_enabled())
            query.side_effect = None
            for value in (0, 1, 42):
                query.return_value = (value, guard_module.winreg.REG_DWORD)
                self.assertEqual(guard_module.automount_enabled(), value == 0)
            query.return_value = ("1", guard_module.winreg.REG_SZ)
            with self.assertRaisesRegex(AutomountError, "DWORD"):
                guard_module.automount_enabled()

    def test_entrypoint_cleanup_precedes_automount_restore(self):
        for fail_at in (None, "construct", "run"):
            policy = Policy()

            class App:
                def __init__(self, _config, policy=policy, fail_at=fail_at):
                    self.policy = policy
                    self.fail_at = fail_at
                    policy.events.append("construct")
                    if fail_at == "construct":
                        raise ValueError("construct")
                    self._window = SimpleNamespace(log=lambda _line: None)

                def run(self):
                    self.policy.events.append("run")
                    if self.fail_at == "run":
                        raise ValueError("run")

                def wait_for_flash_shutdown(self):
                    self.policy.events.append("shutdown")

            @contextmanager
            def marker():
                yield

            with (
                patch.object(entry, "is_admin", return_value=True),
                patch.object(guard_module, "AutomountGuard", side_effect=policy.guard),
                patch.object(app_module, "OdinMApp", App),
                patch.object(config_manager, "ConfigManager", return_value=object()),
                patch.object(runtime_interlock, "register_odin_runtime", marker),
            ):
                if fail_at:
                    with self.assertRaisesRegex(ValueError, fail_at):
                        entry.main()
                else:
                    self.assertEqual(entry.main(), 0)
            self.assertTrue(policy.enabled)
            self.assertEqual(policy.events[-2:], ["/E", "release"])
            if fail_at != "construct":
                self.assertLess(policy.events.index("shutdown"), policy.events.index("/E"))

    def test_shutdown_stops_all_flash_threads_before_returning(self):
        events = []

        def worker(name):
            return SimpleNamespace(
                _thread=SimpleNamespace(
                    is_alive=lambda: True, join=lambda: events.append("join " + name)
                ),
                stop=lambda: events.append("stop " + name),
            )

        app = object.__new__(app_module.OdinMApp)
        app._workers = {0: worker("flash")}
        app._verify_workers = {0: worker("verify")}
        app._guarded_restore = SimpleNamespace(worker=worker("guarded"))
        app._partition_waiters = {0: SimpleNamespace(stop=lambda: events.append("waiter"))}
        app._confirmation = SimpleNamespace(close=lambda: None)
        app.wait_for_flash_shutdown()
        self.assertEqual(
            events,
            [
                "waiter",
                "stop flash",
                "stop verify",
                "stop guarded",
                "join flash",
                "join verify",
                "join guarded",
            ],
        )


if __name__ == "__main__":
    unittest.main()
