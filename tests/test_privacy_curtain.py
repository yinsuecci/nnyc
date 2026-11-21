import unittest
from unittest.mock import Mock, patch

from privacy_curtain import _coverage, Unsupported


class CoverageTests(unittest.TestCase):
    def check(self, other, visible=True, affinity=17, size=(1600,900), cloaked=False):
        user = Mock()
        user.GetTopWindow.return_value = other
        user.GetWindow.return_value = 1
        user.IsWindowVisible.side_effect=lambda w: w == 1 or visible
        def get_affinity(_, ptr):
            ptr._obj.value = affinity
            return 1
        user.GetWindowDisplayAffinity.side_effect = get_affinity
        bounds = (0,0,1600,900)
        with patch("privacy_curtain._normal_desktop"), patch("privacy_curtain._geometry",return_value=size), \
             patch("privacy_curtain._rect",return_value=bounds), patch("privacy_curtain._cloaked",side_effect=lambda w:w != 1 and cloaked):
            _coverage(user,1,(1600,900),bounds,stop_button=2)

    def test_only_our_stop_button_or_compositor_hidden_windows_allowed_on_top(self):
        self.check(1)
        self.check(2)
        self.check(3,cloaked=True)
        with self.assertRaises(Unsupported):
            self.check(3)

    def test_rejects_topology_or_capture_policy_change(self):
        with self.assertRaises(Unsupported):
            self.check(1,size=(1800,900))
        with self.assertRaises(Unsupported):
            self.check(1,affinity=1)
