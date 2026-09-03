import sys, unittest
sys.path.insert(0, "/etc/neronOS")
from unittest import mock
import requests

from doctor import tester, fixer

class TesterProbeTests(unittest.TestCase):
    def test_probe_success(self):
        class DummyResp:
            def __init__(self):
                self.status_code = 200
                class E:
                    def __init__(self): pass
                    def total_seconds(self): return 0.012
                self.elapsed = E()
        with mock.patch('doctor.tester.requests.get', return_value=DummyResp()):
            res = tester._probe("http://example")
            self.assertEqual(res['code'], 200)
            self.assertTrue(res['ok'])
            self.assertIsNone(res['error'])
            self.assertIsNotNone(res['latency_ms'])

    def test_probe_failure(self):
        with mock.patch('doctor.tester.requests.get', side_effect=requests.ConnectionError("fail")):
            res = tester._probe("http://example")
            self.assertIsNone(res['code'])
            self.assertFalse(res['ok'])
            self.assertIsNotNone(res['error'])

class FixerTests(unittest.TestCase):
    def test_no_systemctl(self):
        with mock.patch('doctor.fixer._systemctl_available', return_value=False):
            res = fixer.apply_fixes({})
            self.assertIsInstance(res, list)
            self.assertFalse(res[0].get('ok'))

    def test_no_action_needed(self):
        report = {'tests': {'server_health': {'ok': True}}, 'monitor': {'services': {'neron-server': {'active': True}}}}
        with mock.patch('doctor.fixer._systemctl_available', return_value=True):
            res = fixer.apply_fixes(report)
            self.assertEqual(res, [{'ok': True, 'message': 'no_action_needed'}])

    def test_restart_called(self):
        report = {'tests': {'server_health': {'ok': False}}}
        with mock.patch('doctor.fixer._systemctl_available', return_value=True), \
             mock.patch('doctor.fixer._restart_service', return_value={'service':'neron-server','ok':True,'attempts':1,'message':'restarted_and_active'}):
            res = fixer.apply_fixes(report)
            self.assertTrue(any(r.get('ok') for r in res))

class DoctorDoesNotRepairTests(unittest.TestCase):
    """Doctor analyse, Goal repare.

    Le diagnostic periodique redemarrait des services toutes les 5 min sur un
    seul echec de sonde. Core, qui met ~25 s a demarrer, etait tue avant
    d'avoir pu repondre.
    """

    def setUp(self):
        fixer._last_restart.clear()

    def test_diagnose_reports_without_restarting(self):
        report = {'tests': {'server_health': {'ok': False}}}

        with mock.patch('doctor.fixer._restart_service') as restart, \
             mock.patch('doctor.fixer._seconds_since_start', return_value=None):
            findings = fixer.diagnose_unhealthy(report)

        restart.assert_not_called()
        self.assertEqual(findings[0]['service'], 'neron@core')
        self.assertIsNone(findings[0]['action_taken'])
        self.assertEqual(findings[0]['recommended_action'], 'restart')

    def test_full_diagnosis_never_repairs(self):
        """Le chemin reellement emprunte par le timer ne doit pas corriger."""
        from doctor import runner

        with mock.patch('doctor.fixer._restart_service') as restart, \
             mock.patch('doctor.runner.analyze_project', return_value={}), \
             mock.patch('doctor.runner.get_system_metrics', return_value={}), \
             mock.patch('doctor.runner.get_all_services_status', return_value={}), \
             mock.patch('doctor.runner.get_all_journal_errors', return_value={}), \
             mock.patch('doctor.runner.test_services',
                        return_value={'server_health': {'ok': False}}):
            report = runner.run_full_diagnosis()

        restart.assert_not_called()
        self.assertEqual(report['fixes'][0]['message'], 'diagnosed_not_repaired')

    def test_grace_period_blocks_restart(self):
        """Un service qui demarre n'est pas en faute : il finit son demarrage."""
        report = {'tests': {'server_health': {'ok': False}}}

        with mock.patch('doctor.fixer._systemctl_available', return_value=True), \
             mock.patch('doctor.fixer._seconds_since_start', return_value=5.0), \
             mock.patch('doctor.fixer._restart_service') as restart:
            fixes = fixer.apply_fixes(report)

        restart.assert_not_called()
        self.assertIn('grace_period', fixes[0]['message'])

    def test_cooldown_blocks_second_restart(self):
        report = {'tests': {'server_health': {'ok': False}}}
        stub = {'service': 'neron@core', 'ok': True, 'attempts': 1, 'message': 'restarted_and_active'}

        with mock.patch('doctor.fixer._systemctl_available', return_value=True), \
             mock.patch('doctor.fixer._seconds_since_start', return_value=None), \
             mock.patch('doctor.fixer._restart_service', return_value=stub) as restart:
            first = fixer.apply_fixes(report)
            second = fixer.apply_fixes(report)

        self.assertEqual(restart.call_count, 1, "le second redemarrage doit etre refuse")
        self.assertEqual(first[0]['action_taken'], 'restart')
        self.assertIn('cooldown', second[0]['message'])


# New security tests
try:
    from fastapi.testclient import TestClient
    FASTAPI_AVAILABLE = True
except Exception:
    FASTAPI_AVAILABLE = False

doctor_app = None
if FASTAPI_AVAILABLE:
    from doctor import app as doctor_app
from doctor.config import cfg

@unittest.skipUnless(FASTAPI_AVAILABLE, "fastapi not installed")
class SecurityTests(unittest.TestCase):
    def test_config_masks_api_key(self):
        # enable auth and set a known API key
        cfg.API_KEY = "supersecret"
        client = TestClient(doctor_app.app)
        res = client.get("/config", headers={"X-Doctor-Key": "supersecret"})
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("API_KEY", data)
        self.assertNotEqual(data["API_KEY"], "supersecret")
        self.assertTrue("MASKED" in str(data["API_KEY"]))


    def test_logger_masks_api_key(self):
        import io, logging
        from doctor.logger import RedactingFilter
        # set api key
        cfg.API_KEY = "topsecret"
        buf = io.StringIO()
        handler = logging.StreamHandler(buf)
        handler.setFormatter(logging.Formatter("%(message)s"))
        handler.addFilter(RedactingFilter())
        logger = logging.getLogger("test.redact")
        # ensure isolated handlers
        logger.handlers = []
        logger.setLevel(logging.INFO)
        logger.addHandler(handler)
        logger.info("api key is %s", cfg.API_KEY)
        handler.flush()
        out = buf.getvalue()
        self.assertNotIn("topsecret", out)
        self.assertIn("***MASKED***", out)

if __name__ == '__main__':
    unittest.main()
