import importlib
import os
import tempfile
import unittest


class FlaskSandboxTestbench(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["SANDBOX_MODE"] = "true"
        cls._tmp_dir = tempfile.TemporaryDirectory()
        os.environ["RUNTIME_ENV_PATH"] = os.path.join(cls._tmp_dir.name, "runtime.env")
        import app as app_module

        cls.app_module = importlib.reload(app_module)
        cls.client = cls.app_module.app.test_client()

    @classmethod
    def tearDownClass(cls):
        cls._tmp_dir.cleanup()

    def test_root_renders(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"NTP Server Control", response.data)

    def test_system_status(self):
        response = self.client.get("/api/system/status")
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertTrue(body["success"])
        self.assertIn("temperature", body)
        self.assertIn("load", body)
        self.assertIn("uptime", body)
        self.assertIn("chrony_active", body)
        self.assertIn("stratum", body)

    def test_wan_validation_rejects_invalid_ip(self):
        response = self.client.post(
            "/api/network/wan",
            json={"mode": "static", "ip": "bad-ip", "subnet": "255.255.255.0"},
        )
        self.assertEqual(response.status_code, 400)
        body = response.get_json()
        self.assertFalse(body["success"])

    def test_interface_mapping_get(self):
        response = self.client.get("/api/network/interfaces")
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertTrue(body["success"])
        self.assertIn("mapping", body)
        self.assertIn("wan", body["mapping"])
        self.assertIn("lan", body["mapping"])

    def test_interface_mapping_rejects_same_interface(self):
        response = self.client.post(
            "/api/network/interfaces",
            json={"wan": "eth0", "lan": "eth0", "wifi": "wlan0"},
        )
        self.assertEqual(response.status_code, 400)
        body = response.get_json()
        self.assertFalse(body["success"])

    def test_interface_mapping_swap_success(self):
        response = self.client.post(
            "/api/network/interfaces",
            json={"wan": "eth1", "lan": "eth0", "wifi": "wlan0"},
        )
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertTrue(body["success"])
        self.assertEqual(body["mapping"]["wan"], "eth1")
        self.assertEqual(body["mapping"]["lan"], "eth0")

    def test_wan_dhcp_success(self):
        response = self.client.post("/api/network/wan", json={"mode": "dhcp"})
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertTrue(body["success"])

    def test_wifi_scan_success(self):
        response = self.client.get("/api/network/wifi/scan")
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertTrue(body["success"])
        self.assertGreaterEqual(len(body["networks"]), 1)

    def test_wifi_connect_validation(self):
        response = self.client.post("/api/network/wifi", json={"ssid": "", "password": "x"})
        self.assertEqual(response.status_code, 400)
        body = response.get_json()
        self.assertFalse(body["success"])

    def test_wifi_static_validation_rejects_bad_ip(self):
        response = self.client.post(
            "/api/network/wifi",
            json={
                "mode": "static",
                "ssid": "HomeLab",
                "password": "password",
                "ip": "bad-ip",
                "subnet": "255.255.255.0",
            },
        )
        self.assertEqual(response.status_code, 400)
        body = response.get_json()
        self.assertFalse(body["success"])

    def test_lan_update_success(self):
        response = self.client.post(
            "/api/network/lan",
            json={"ip": "10.0.0.10", "subnet": "255.255.255.0"},
        )
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertTrue(body["success"])

    def test_ntp_restart_success(self):
        response = self.client.post("/api/service/ntp", json={"action": "restart"})
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertTrue(body["success"])
        self.assertIn("service", body)

    def test_reboot_success(self):
        response = self.client.post("/api/service/reboot", json={})
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertTrue(body["success"])


if __name__ == "__main__":
    unittest.main()
