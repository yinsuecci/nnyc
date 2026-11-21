import unittest
from unittest.mock import Mock

from privacy_audio import MuteLease, guard


class Playback:
    def __init__(self, muted, refuse=False):
        self.muted = muted
        self.refuse = refuse

    def GetMute(self):
        return self.muted

    def SetMute(self, value, _context):
        if not self.refuse:
            self.muted = bool(value)


class AudioLeaseTests(unittest.TestCase):
    def test_new_device_and_originally_muted_endpoint(self):
        endpoints = {"speakers": Playback(False), "headset": Playback(True)}
        lease = MuteLease(lambda: endpoints)
        lease.enforce()
        endpoints["HDMI"] = Playback(False)
        lease.enforce()
        self.assertTrue(all(e.muted for e in endpoints.values()))
        lease.restore()
        self.assertEqual([False, True, False], [e.muted for e in endpoints.values()])

    def test_failure_before_ack_rolls_back_already_changed_outputs(self):
        endpoints = {"speakers": Playback(False), "broken": Playback(False, refuse=True)}
        pipe = Mock()
        guard(pipe, lambda: MuteLease(lambda: endpoints))
        self.assertFalse(endpoints["speakers"].muted)
        self.assertEqual([("failed",), ("restored",)], [x.args for x in pipe.send.call_args_list])

    def test_client_pipe_closed_restores_without_a_stop_command(self):
        endpoint = Playback(False)
        pipe = Mock()
        pipe.poll.return_value = True
        def send(status):
            if status == "muted":
                self.assertTrue(endpoint.muted)
            else:
                raise BrokenPipeError()
        pipe.send.side_effect = send
        guard(pipe, lambda: MuteLease(lambda: {"one": endpoint}))
        self.assertFalse(endpoint.muted)
        pipe.close.assert_called_once()

    def test_missing_endpoint_is_not_reported_as_muted(self):
        with self.assertRaises(RuntimeError):
            MuteLease(dict).enforce()
