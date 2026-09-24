"""Relecture de la séquence : un refus de la carte doit être signalé."""
import sys
import unittest

from PySide6.QtCore import QCoreApplication

sys.path.insert(0, ".")
from src.controllers.device_controller import DeviceController  # noqa: E402


class SequenceReadbackTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def setUp(self):
        self.device = DeviceController()
        self.device.recordDurationMin = 10
        self.device.pauseDurationMin = 30
        self.device._sent_sequence = (10, 30)

    def test_board_keeping_old_values_is_reported(self):
        self.device._apply_sequence_reply("Duree d'enregistrement : 50 min\nDuree de veille : 50 min")
        self.assertEqual((self.device.recordDurationMin, self.device.pauseDurationMin), (50, 50))
        self.assertIn("n'a pas appliqué", self.device.lastError)
        self.assertIn("enregistrement 10 min (la carte garde 50 min)", self.device.lastError)
        self.assertIn("veille 30 min (la carte garde 50 min)", self.device.lastError)

    def test_board_applying_values_raises_no_error(self):
        self.device._apply_sequence_reply("Duree d'enregistrement : 10 min\nDuree de veille : 30 min")
        self.assertEqual(self.device.lastError, "")
        self.assertTrue(self.device.sequenceSynced)

    def test_plain_read_does_not_compare(self):
        self.device._sent_sequence = None
        self.device._apply_sequence_reply("rec : 50 min\nveille : 50 min")
        self.assertEqual(self.device.lastError, "")


if __name__ == "__main__":
    unittest.main()
