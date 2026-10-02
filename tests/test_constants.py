import json

from helpers import KiasiTestCase
from core import constants  # noqa: E402


class TestProjectOverrides(KiasiTestCase):
    def setUp(self):
        super().setUp()
        self.before = {name: getattr(constants, name) for name in constants.PROJECT_KEYS.values()}

    def tearDown(self):
        for name, value in self.before.items():
            setattr(constants, name, value)
        super().tearDown()

    def test_kiasi_json_tunes_caps_and_budgets(self):
        (self.tmp / constants.PROJECT_FILE_NAME).write_text(json.dumps(
            {"output_cap_chars": 20_000, "turn_call_budget": 80, "mystery_knob": 7, "mcp_cap_chars": -1}))
        applied = constants.apply_project(self.tmp)
        self.assertEqual(constants.CAP_OUTSIDE_READ_CHARS, 20_000)
        self.assertEqual(constants.TURN_STOP_STEPS, 80)
        self.assertEqual(constants.CAP_MCP_CHARS, self.before["CAP_MCP_CHARS"], "non-positive values are ignored")
        self.assertEqual(set(applied), {"CAP_OUTSIDE_READ_CHARS", "TURN_STOP_STEPS"})

    def test_missing_or_broken_file_changes_nothing(self):
        self.assertEqual(constants.apply_project(self.tmp), {})
        (self.tmp / constants.PROJECT_FILE_NAME).write_text("{not json")
        self.assertEqual(constants.apply_project(self.tmp), {})
        self.assertEqual(constants.TURN_STOP_STEPS, self.before["TURN_STOP_STEPS"])
