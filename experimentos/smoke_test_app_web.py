# -*- coding: utf-8 -*-
"""Smoke tests rápidos para app_web sin depender de pytest ni BLE real."""

import unittest

import app_web


class _FakeBridge:
    def __init__(self):
        self.connected = True
        self.robot_type_name = "dash"
        self.robot_label = "FakeDash"
        self.last_drive = (0.0, 0.0)
        self.last_head = (0.0, 0.0)
        self.last_lights = (0.0, 0.0, 0.0)

    def drive(self, linear, angular):
        self.last_drive = (linear, angular)

    def head(self, pan, tilt):
        self.last_head = (pan, tilt)

    def lights(self, r, g, b):
        self.last_lights = (r, g, b)

    def stop(self, record=False):
        self.last_drive = (0.0, 0.0)

    def play_sound(self, name):
        return name


class _FakeMotion:
    def __init__(self, bridge):
        self.bridge = bridge

    def stop_now(self):
        self.bridge.stop()


class _FakeRecorder:
    recording = False

    @staticmethod
    def start():
        return None

    @staticmethod
    def stop():
        return ([], 0)

    @staticmethod
    def elapsed_ms():
        return 0

    @staticmethod
    def step_count():
        return 0


class _FakeCtl:
    def __init__(self):
        self.bridge = _FakeBridge()
        self.motion = _FakeMotion(self.bridge)
        self.recorder = _FakeRecorder()
        self.avoiding = False
        self.avoid_enabled = False
        self.explore_enabled = False
        self.explore_profile = "normal"
        self.agent_busy = False
        self.state = "connected"
        self.last_distance = 30.0
        self.last_distance_rear = 40.0

    def status(self):
        return {
            "state": self.state,
            "connected": self.bridge.connected,
            "signal": True,
            "robot_pref": "any",
            "robot": self.bridge.robot_label,
            "search_s": 0,
            "recording": False,
            "rec_ms": 0,
            "rec_steps": 0,
            "replay": {"playing": False},
            "avoid": self.avoid_enabled,
            "avoiding": self.avoiding,
            "explore": self.explore_enabled,
            "explore_profile": self.explore_profile,
            "explore_ai": {"profile": self.explore_profile, "recent": []},
            "distance": self.last_distance,
            "distance_rear": self.last_distance_rear,
            "alert": {},
            "agent_busy": self.agent_busy,
        }


class SmokeAppWeb(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._old_ctl = app_web.ctl
        cls._old_api_key = app_web.SEC_API_KEY
        cls._old_allow_public = app_web.SEC_ALLOW_PUBLIC
        cls._old_trust_proxy = app_web.SEC_TRUST_PROXY
        cls._old_allow_nets = list(app_web.SEC_ALLOW_NETS)

        app_web.ctl = _FakeCtl()
        app_web.SEC_ALLOW_PUBLIC = True
        app_web.SEC_TRUST_PROXY = False
        app_web.SEC_ALLOW_NETS = []
        app_web.SEC_API_KEY = ""
        app_web.app.config["TESTING"] = True
        cls.client = app_web.app.test_client()

    @classmethod
    def tearDownClass(cls):
        app_web.ctl = cls._old_ctl
        app_web.SEC_API_KEY = cls._old_api_key
        app_web.SEC_ALLOW_PUBLIC = cls._old_allow_public
        app_web.SEC_TRUST_PROXY = cls._old_trust_proxy
        app_web.SEC_ALLOW_NETS = cls._old_allow_nets
        app_web.app.config["TESTING"] = False

    def test_healthz(self):
        r = self.client.get("/healthz")
        self.assertEqual(r.status_code, 200)
        j = r.get_json()
        self.assertTrue(j["ok"])
        self.assertIn("connected", j)

    def test_status(self):
        r = self.client.get("/api/status")
        self.assertEqual(r.status_code, 200)
        j = r.get_json()
        self.assertTrue(j["connected"])
        self.assertIn("duo", j)

    def test_drive_clamps_values(self):
        r = self.client.post("/api/drive", json={"linear": 999, "angular": -999})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(app_web.ctl.bridge.last_drive, (80.0, -220.0))

    def test_head_and_lights_clamp(self):
        self.client.post("/api/head", json={"pan": 400, "tilt": -999})
        self.assertEqual(app_web.ctl.bridge.last_head, (120.0, -10.0))

        self.client.post("/api/lights", json={"r": -1, "g": 2.5, "b": 0.4})
        self.assertEqual(app_web.ctl.bridge.last_lights, (0.0, 1.0, 0.4))

    def test_api_key_guard(self):
        app_web.SEC_API_KEY = "secreto"
        r = self.client.get("/api/status")
        self.assertEqual(r.status_code, 401)

        r2 = self.client.get("/api/status", headers={"X-API-Key": "secreto"})
        self.assertEqual(r2.status_code, 200)
        app_web.SEC_API_KEY = ""


if __name__ == "__main__":
    unittest.main(verbosity=2)
