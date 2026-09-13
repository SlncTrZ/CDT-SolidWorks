import unittest

from cdt_solidworks.native.models import ApplicationOwnership, NativeCallState
from cdt_solidworks.native.session import AttachPolicy, SolidWorksSession


class FakeComApi:
    def __init__(self, *, attach_app=None, start_app=None, registered=True) -> None:
        self.attach_app = attach_app
        self.start_app = start_app if start_app is not None else object()
        self.registered = registered
        self.initialized = 0
        self.uninitialized = 0
        self.exited = []
        self.visible = []

    def initialize_thread(self) -> None:
        self.initialized += 1

    def uninitialize_thread(self) -> None:
        self.uninitialized += 1

    def prog_id_registered(self, prog_id: str) -> bool:
        return self.registered

    def attach_application(self, prog_id: str):
        if self.attach_app is None:
            raise RuntimeError("no running instance")
        return self.attach_app

    def start_application(self, prog_id: str):
        return self.start_app

    def set_visible(self, app, visible: bool) -> None:
        self.visible.append((app, visible))

    def revision_number(self, app) -> str:
        return "34.1.1"

    def exit_application(self, app) -> None:
        self.exited.append(app)


class SolidWorksSessionTests(unittest.TestCase):
    def test_probe_reports_registered_running_instance_without_launch(self) -> None:
        existing = object()
        api = FakeComApi(attach_app=existing, registered=True)
        session = SolidWorksSession(api=api)
        try:
            result = session.probe(version=2026, timeout=0.5)
            self.assertEqual(NativeCallState.SUCCESS, result.state)
            self.assertTrue(result.value.registered)
            self.assertTrue(result.value.running)
            self.assertEqual("34.1.1", result.value.revision)
            self.assertFalse(session.connected)
            self.assertEqual([], api.visible)
        finally:
            session.close_dispatcher(timeout=0.5)

    def test_probe_prefers_bound_session_when_provider_owned_instance_is_not_in_rot(self) -> None:
        started = object()
        api = FakeComApi(start_app=started, attach_app=None, registered=True)
        session = SolidWorksSession(api=api)
        try:
            connected = session.connect(policy=AttachPolicy.START_NEW, timeout=0.5)
            self.assertEqual(NativeCallState.SUCCESS, connected.state)

            probed = session.probe(version=2026, timeout=0.5)

            self.assertEqual(NativeCallState.SUCCESS, probed.state)
            self.assertTrue(probed.value.registered)
            self.assertTrue(probed.value.running)
            self.assertEqual("34.1.1", probed.value.revision)
        finally:
            session.disconnect(timeout=0.5)
            session.close_dispatcher(timeout=0.5)

    def test_probe_reports_registered_but_not_running_without_starting(self) -> None:
        api = FakeComApi(registered=True)
        session = SolidWorksSession(api=api)
        try:
            result = session.probe(timeout=0.5)
            self.assertEqual(NativeCallState.SUCCESS, result.state)
            self.assertTrue(result.value.registered)
            self.assertFalse(result.value.running)
            self.assertFalse(session.connected)
            self.assertEqual([], api.visible)
        finally:
            session.close_dispatcher(timeout=0.5)

    def test_attach_existing_is_user_owned_and_never_exited(self) -> None:
        existing = object()
        api = FakeComApi(attach_app=existing)
        session = SolidWorksSession(api=api)
        try:
            connected = session.connect(policy=AttachPolicy.ATTACH_OR_START, timeout=0.5)
            self.assertEqual(NativeCallState.SUCCESS, connected.state)
            self.assertEqual(ApplicationOwnership.USER_OWNED, connected.value.ownership)
            self.assertEqual(2026, connected.value.version_year)

            disconnected = session.disconnect(timeout=0.5)
            self.assertEqual(NativeCallState.SUCCESS, disconnected.state)
            self.assertEqual([], api.exited)
        finally:
            session.close_dispatcher(timeout=0.5)

    def test_fallback_start_is_provider_owned_and_exited_on_disconnect(self) -> None:
        started = object()
        api = FakeComApi(start_app=started)
        session = SolidWorksSession(api=api)
        try:
            connected = session.connect(policy=AttachPolicy.ATTACH_OR_START, visible=False, timeout=0.5)
            self.assertEqual(NativeCallState.SUCCESS, connected.state)
            self.assertEqual(ApplicationOwnership.PROVIDER_OWNED, connected.value.ownership)
            self.assertEqual([(started, False)], api.visible)

            disconnected = session.disconnect(timeout=0.5)
            self.assertEqual(NativeCallState.SUCCESS, disconnected.state)
            self.assertEqual([started], api.exited)
        finally:
            session.close_dispatcher(timeout=0.5)

    def test_attach_only_failure_does_not_start_application(self) -> None:
        api = FakeComApi()
        session = SolidWorksSession(api=api)
        try:
            result = session.connect(policy=AttachPolicy.ATTACH_ONLY, timeout=0.5)
            self.assertEqual(NativeCallState.FAILURE, result.state)
            self.assertEqual("solidworks_attach_failed", result.failure.code)
            self.assertFalse(session.connected)
        finally:
            session.close_dispatcher(timeout=0.5)

    def test_prog_id_version_mapping_is_explicit(self) -> None:
        self.assertEqual("SldWorks.Application", SolidWorksSession.prog_id_for_version(None))
        self.assertEqual("SldWorks.Application.34", SolidWorksSession.prog_id_for_version(2026))
        with self.assertRaises(ValueError):
            SolidWorksSession.prog_id_for_version(2001)


if __name__ == "__main__":
    unittest.main()
