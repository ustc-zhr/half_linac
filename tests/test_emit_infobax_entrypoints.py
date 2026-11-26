"""Production real entry delegates to the framework; Fake remains isolated."""
from contextlib import redirect_stderr
from io import StringIO
import unittest
from unittest.mock import patch
from repo_bootstrap import ensure_repo_import_path
ensure_repo_import_path(__file__)
from half_linac.src.apps.emit_measure import infobax_launcher as launcher


class EntryTests(unittest.TestCase):
    def test_real_entry_routes_to_the_existing_application(self):
        with patch.object(launcher,'launch_framework_gui',return_value=17) as launch:
            self.assertEqual(launcher.main(['--backend','real']),17)
            launch.assert_called_once_with()

    def test_real_headless_mode_still_rejected(self):
        with redirect_stderr(StringIO()),self.assertRaises(SystemExit) as raised:
            launcher.main(['--backend','real','--headless','--approve-fake'])
        self.assertEqual(raised.exception.code,2)

    def test_legacy_real_factory_cannot_create_a_second_live_pipeline(self):
        with patch('epics.caget',side_effect=AssertionError('No reads')),patch('epics.caput',side_effect=AssertionError('No writes')):
            with self.assertRaisesRegex(ValueError,'main Emittance Optimization'):
                launcher.build_workflow({'backend':'real'},'/tmp/unused-infobax-entry')
