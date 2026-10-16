import importlib
import os
import pathlib
import re
import tempfile
import unittest
from unittest.mock import patch


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

    def test_root_uses_local_assets_only(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        html = response.data.decode("utf-8")

        for asset_path in (
            "/static/assets/tailwind.min.js",
            "/static/assets/fontawesome.css",
            "/static/assets/site.css",
        ):
            self.assertIn(asset_path, html)

        self.assertNotRegex(html, r"https?://")
        self.assertNotRegex(html, r"cdn")

    def test_static_assets_resolve(self):
        asset_paths = (
            "/static/assets/tailwind.min.js",
            "/static/assets/fontawesome.css",
            "/static/assets/site.css",
            "/static/assets/fonts/fa-solid-900.woff2",
            "/static/assets/fonts/Inter-roman.var.woff2",
            "/static/assets/fonts/Inter-italic.var.woff2",
        )

        for asset_path in asset_paths:
            with self.subTest(asset_path=asset_path):
                response = self.client.get(asset_path)
                self.assertEqual(response.status_code, 200)

    def test_static_asset_mime_types(self):
        css_response = self.client.get("/static/assets/site.css")
        self.assertIn("text/css", css_response.content_type)

        js_response = self.client.get("/static/assets/tailwind.min.js")
        self.assertTrue(re.search(r"javascript|ecmascript", js_response.content_type))

    def test_ui_files_do_not_include_remote_assets(self):
        repo_root = pathlib.Path(__file__).resolve().parents[1]
        file_patterns = (
            "templates/*.html",
            "static/assets/*.css",
        )
        remote_pattern = re.compile(r"https?://|cdn\\.", re.IGNORECASE)

        for pattern in file_patterns:
            for file_path in repo_root.glob(pattern):
                with self.subTest(file=str(file_path.relative_to(repo_root))):
                    content = file_path.read_text(encoding="utf-8")
                    self.assertIsNone(
                        remote_pattern.search(content),
                        msg=f"Remote asset reference found in {file_path}",
                    )

    def test_template_icon_classes_have_local_mappings(self):
        repo_root = pathlib.Path(__file__).resolve().parents[1]
        template_content = (repo_root / "templates" / "index.html").read_text(encoding="utf-8")
        icon_css_content = (repo_root / "static" / "assets" / "fontawesome.css").read_text(encoding="utf-8")

        # Collect icon classes from HTML class attributes only.
        template_icons = set()
        class_values = re.findall(r'class\s*=\s*["\']([^"\']+)["\']', template_content)
        for class_value in class_values:
            for token in class_value.split():
                if token.startswith("fa-"):
                    icon_name = token[3:]
                    if icon_name not in {"solid", "spin"}:
                        template_icons.add(icon_name)

        # Collect mapped glyph class names from local icon stylesheet.
        mapped_icons = set(re.findall(r"\.fa-([a-z0-9-]+)::before", icon_css_content))
        missing = sorted(template_icons - mapped_icons)
        self.assertEqual(
            missing,
            [],
            msg=f"Missing local icon mappings for: {', '.join(missing)}",
        )

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

    def test_wifi_scan_reports_real_failure(self):
        with patch.object(self.app_module, "SANDBOX_MODE", False), patch.object(
            self.app_module, "run_cmd", return_value=(False, "nmcli not available")
        ):
            response = self.client.get("/api/network/wifi/scan")
        self.assertEqual(response.status_code, 500)
        body = response.get_json()
        self.assertFalse(body["success"])
        self.assertIn("nmcli not available", body["error"])

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
            json={"ip": "10.12.0.10", "subnet": "255.255.0.0"},
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

    def test_systemd_service_grants_capabilities_needed_for_sudo(self):
        service_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "deploy", "ntp-webgui.service"
        )
        with open(service_path, "r", encoding="utf-8") as handle:
            content = handle.read()

        for required in (
            "CAP_SETGID",
            "CAP_SETUID",
            "CAP_AUDIT_WRITE",
            "SecureBits=keep-caps",
        ):
            self.assertIn(required, content)


if __name__ == "__main__":
    unittest.main()
