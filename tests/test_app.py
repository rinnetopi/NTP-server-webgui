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
        os.environ["CREDENTIALS_PATH"] = os.path.join(cls._tmp_dir.name, "credentials.json")
        os.environ["SECRET_KEY_PATH"] = os.path.join(cls._tmp_dir.name, "secret_key")
        import app as app_module

        cls.app_module = importlib.reload(app_module)
        cls.client = cls.app_module.app.test_client()
        response = cls.client.post("/login", data={"username": "admin", "password": "admin"})
        assert response.status_code == 302, "default admin/admin login failed"

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

        self.assertIn("/static/assets/site.css", html)
        self.assertNotRegex(html, r"https?://")
        self.assertNotRegex(html, r"cdn")

    def test_static_assets_resolve(self):
        asset_paths = (
            "/static/assets/site.css",
            "/static/assets/fonts/Inter-roman.var.woff2",
            "/static/assets/fonts/Inter-italic.var.woff2",
        )

        for asset_path in asset_paths:
            with self.subTest(asset_path=asset_path):
                response = self.client.get(asset_path)
                self.assertEqual(response.status_code, 200)
                response.close()

    def test_static_asset_mime_types(self):
        css_response = self.client.get("/static/assets/site.css")
        self.assertIn("text/css", css_response.content_type)
        css_response.close()

    def test_ui_files_do_not_include_remote_assets(self):
        repo_root = pathlib.Path(__file__).resolve().parents[1]
        file_patterns = (
            "templates/*.html",
            "static/assets/*.css",
        )
        remote_pattern = re.compile(r"https?://|cdn\.", re.IGNORECASE)

        for pattern in file_patterns:
            for file_path in repo_root.glob(pattern):
                with self.subTest(file=str(file_path.relative_to(repo_root))):
                    content = file_path.read_text(encoding="utf-8")
                    self.assertIsNone(
                        remote_pattern.search(content),
                        msg=f"Remote asset reference found in {file_path}",
                    )

    def test_template_svg_icons_are_defined(self):
        repo_root = pathlib.Path(__file__).resolve().parents[1]
        template = (repo_root / "templates" / "index.html").read_text(encoding="utf-8")
        used = set(re.findall(r'href=["\']#(i-[a-z0-9-]+)', template))
        used |= set(re.findall(r"'#(i-[a-z0-9-]+)'", template))
        defined = set(re.findall(r'<symbol id="(i-[a-z0-9-]+)"', template))
        self.assertEqual(sorted(used - defined), [])

    def test_template_element_ids_used_by_script_exist(self):
        repo_root = pathlib.Path(__file__).resolve().parents[1]
        template = (repo_root / "templates" / "index.html").read_text(encoding="utf-8")
        referenced = set(re.findall(r"getElementById\('([a-z0-9-]+)'\)", template))
        defined = set(re.findall(r'id="([a-z0-9-]+)"', template))
        # prefixes built dynamically in setMode()
        dynamic = {"-static-fields", "-dhcp-lbl", "-static-lbl"}
        missing = sorted(i for i in referenced - defined if not any(i.endswith(d) for d in dynamic))
        self.assertEqual(missing, [])

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

    def test_current_network_values_endpoint(self):
        def fake_run_cmd(cmd_list):
            if cmd_list == ["ip", "-4", "-o", "addr", "show", "dev", "eth0"]:
                return True, "2: eth0    inet 192.168.1.239/24 brd 192.168.1.255 scope global eth0"
            if cmd_list == ["ip", "-4", "-o", "addr", "show", "dev", "eth1"]:
                return True, "3: eth1    inet 10.12.0.10/16 brd 10.12.255.255 scope global eth1"
            if cmd_list == ["ip", "-4", "-o", "addr", "show", "dev", "wlan0"]:
                return False, "no address"
            return True, "ok"

        with patch.object(self.app_module, "SANDBOX_MODE", False), patch.object(
            self.app_module, "run_cmd", side_effect=fake_run_cmd
        ):
            response = self.client.get("/api/network/current")

        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertTrue(body["success"])
        self.assertEqual(body["addresses"]["wan"], "192.168.1.239")
        self.assertEqual(body["addresses"]["lan"], "10.12.0.10")
        self.assertEqual(body["addresses"]["wifi"], "")

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

    def test_lan_update_requires_payload_values(self):
        response = self.client.post("/api/network/lan", json={})
        self.assertEqual(response.status_code, 400)
        body = response.get_json()
        self.assertFalse(body["success"])
        self.assertIn("required", body["error"].lower())

    def test_lan_update_applies_nmcli_commands(self):
        calls = []

        def fake_run_cmd(cmd_list):
            calls.append(cmd_list)
            if cmd_list[:3] == ["sudo", "nmcli", "con"] and cmd_list[3:5] == ["modify", "eth1"]:
                return True, "ok"
            if cmd_list[:4] == ["sudo", "nmcli", "con", "up"]:
                return True, "up ok"
            return True, "ok"

        with patch.object(self.app_module, "run_cmd", side_effect=fake_run_cmd):
            response = self.client.post(
                "/api/network/lan",
                json={"ip": "10.12.0.20", "subnet": "255.255.0.0"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["success"])
        self.assertTrue(
            any(
                len(cmd) >= 7
                and cmd[:4] == ["sudo", "nmcli", "con", "modify"]
                and cmd[5] == "ipv4.addresses"
                and "10.12.0.20/16" in cmd
                for cmd in calls
            ),
            "LAN IP change did not apply nmcli modify with expected address.",
        )
        self.assertTrue(
            any(len(cmd) == 5 and cmd[:4] == ["sudo", "nmcli", "con", "up"] for cmd in calls),
            "LAN IP change did not bring the LAN connection back up.",
        )

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

    def test_wan_static_applies_gateway_and_dns(self):
        calls = []

        def fake_run_cmd(cmd_list):
            calls.append(cmd_list)
            return True, "ok"

        with patch.object(self.app_module, "run_cmd", side_effect=fake_run_cmd):
            response = self.client.post(
                "/api/network/wan",
                json={
                    "mode": "static",
                    "ip": "192.168.1.50",
                    "subnet": "255.255.255.0",
                    "gateway": "192.168.1.1",
                    "dns": "1.1.1.1, 8.8.8.8",
                },
            )
        self.assertEqual(response.status_code, 200)
        modify = next(c for c in calls if c[:4] == ["sudo", "nmcli", "con", "modify"])
        self.assertIn("192.168.1.50/24", modify)
        self.assertEqual(modify[modify.index("ipv4.gateway") + 1], "192.168.1.1")
        self.assertEqual(modify[modify.index("ipv4.dns") + 1], "1.1.1.1 8.8.8.8")

    def test_wan_static_rejects_bad_gateway(self):
        response = self.client.post(
            "/api/network/wan",
            json={"mode": "static", "ip": "192.168.1.50", "subnet": "255.255.255.0", "gateway": "x"},
        )
        self.assertEqual(response.status_code, 400)

    def test_wan_dhcp_clears_static_values(self):
        calls = []
        with patch.object(self.app_module, "run_cmd", side_effect=lambda c: (calls.append(c), (True, "ok"))[1]):
            response = self.client.post("/api/network/wan", json={"mode": "dhcp"})
        self.assertEqual(response.status_code, 200)
        modify = next(c for c in calls if c[:4] == ["sudo", "nmcli", "con", "modify"])
        self.assertEqual(modify[modify.index("ipv4.method") + 1], "auto")
        self.assertEqual(modify[modify.index("ipv4.addresses") + 1], "")

    def test_wifi_static_validated_before_connecting(self):
        calls = []
        with patch.object(self.app_module, "run_cmd", side_effect=lambda c: (calls.append(c), (True, "ok"))[1]):
            response = self.client.post(
                "/api/network/wifi",
                json={"mode": "static", "ssid": "HomeLab", "password": "pw", "ip": "bad", "subnet": "255.255.255.0"},
            )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(calls, [], "Wi-Fi must not connect when static settings are invalid")

    def test_wifi_open_network_omits_password(self):
        calls = []
        with patch.object(self.app_module, "run_cmd", side_effect=lambda c: (calls.append(c), (True, "ok"))[1]):
            response = self.client.post("/api/network/wifi", json={"mode": "dhcp", "ssid": "OpenNet", "password": ""})
        self.assertEqual(response.status_code, 200)
        connect = next(c for c in calls if "connect" in c)
        self.assertNotIn("password", connect)

    def test_resolve_connection_uses_profile_uuid(self):
        def fake_run_cmd(cmd_list):
            if cmd_list[:3] == ["nmcli", "-g", "GENERAL.CON-UUID"]:
                return True, "559a654f-f996-42eb-8191-2022b239eb77"
            return True, "ok"

        with patch.object(self.app_module, "SANDBOX_MODE", False), patch.object(
            self.app_module, "run_cmd", side_effect=fake_run_cmd
        ):
            self.assertEqual(
                self.app_module.resolve_connection("eth1"), "559a654f-f996-42eb-8191-2022b239eb77"
            )

    def test_lan_update_uses_resolved_profile_and_clears_gateway(self):
        calls = []

        def fake_run_cmd(cmd_list):
            calls.append(cmd_list)
            if cmd_list[:3] == ["nmcli", "-g", "GENERAL.CON-UUID"]:
                return True, "uuid-lan"
            return True, "ok"

        roles = {"wan": "eth0", "lan": "eth1", "wifi": "wlan0"}
        with patch.object(self.app_module, "SANDBOX_MODE", False), patch.object(
            self.app_module, "INTERFACE_ROLES", roles
        ), patch.object(self.app_module, "run_cmd", side_effect=fake_run_cmd):
            response = self.client.post("/api/network/lan", json={"ip": "10.12.0.1", "subnet": "255.255.0.0"})
        self.assertEqual(response.status_code, 200)
        modify = next(c for c in calls if c[:4] == ["sudo", "nmcli", "con", "modify"])
        self.assertEqual(modify[4], "uuid-lan")
        self.assertEqual(modify[modify.index("ipv4.gateway") + 1], "")
        self.assertIn(["sudo", "nmcli", "con", "up", "uuid-lan"], calls)

    def test_lan_update_refreshes_proxy_dns_and_chrony(self):
        calls = []
        with patch.object(self.app_module, "run_cmd", side_effect=lambda c: (calls.append(c), (True, "ok"))[1]):
            response = self.client.post("/api/network/lan", json={"ip": "10.20.0.1", "subnet": "255.255.255.0"})
        self.assertEqual(response.status_code, 200)
        self.assertIn(["sudo", self.app_module.LAN_REFRESH_HELPER, "10.20.0.1", "24"], calls)

    def test_lan_refresh_helper_is_whitelisted_in_sudoers(self):
        repo_root = pathlib.Path(__file__).resolve().parents[1]
        user_script = (repo_root / "scripts" / "setup_ntp_user.sh").read_text(encoding="utf-8")
        self.assertIn("NOPASSWD: /usr/local/sbin/ntp-webgui-lan-refresh", user_script)

    def test_lan_refuses_when_lan_equals_wan(self):
        roles = {"wan": "eth0", "lan": "eth0", "wifi": "wlan0"}
        with patch.object(self.app_module, "INTERFACE_ROLES", roles):
            response = self.client.post("/api/network/lan", json={"ip": "10.12.0.1", "subnet": "255.255.0.0"})
        self.assertEqual(response.status_code, 400)

    def test_detect_wan_interface_parses_ip_route(self):
        class Result:
            stdout = "default via 192.168.1.1 dev eth1 proto dhcp src 192.168.1.239 metric 100\n"
            returncode = 0

        with patch.object(self.app_module.subprocess, "run", return_value=Result()):
            self.assertEqual(self.app_module.detect_wan_interface(), "eth1")

    def test_wifi_scan_handles_escaped_colons_and_duplicates(self):
        output = "Cafe\\:Guest:40:WPA2\nCafe\\:Guest:70:WPA2\nOpenNet:55:"
        with patch.object(self.app_module, "SANDBOX_MODE", False), patch.object(
            self.app_module, "run_cmd", return_value=(True, output)
        ):
            body = self.client.get("/api/network/wifi/scan").get_json()
        self.assertEqual([n["ssid"] for n in body["networks"]], ["Cafe:Guest", "OpenNet"])
        self.assertEqual(body["networks"][0]["signal"], "70")
        self.assertEqual(body["networks"][1]["security"], "Open")

    # ------------------------------------------------------------------ auth
    def test_pages_and_api_require_login(self):
        anon = self.app_module.app.test_client()
        self.assertEqual(anon.get("/").status_code, 302)
        self.assertIn("/login", anon.get("/").headers["Location"])
        response = anon.get("/api/network/current")
        self.assertEqual(response.status_code, 401)
        self.assertFalse(response.get_json()["success"])
        self.assertEqual(anon.post("/api/service/reboot", json={}).status_code, 401)
        self.assertEqual(anon.get("/login").status_code, 200)
        self.assertEqual(anon.get("/static/assets/site.css").status_code, 200)

    def test_wrong_password_rejected(self):
        anon = self.app_module.app.test_client()
        with patch.object(self.app_module.time, "sleep"):
            response = anon.post("/login", data={"username": "admin", "password": "nope"})
        self.assertEqual(response.status_code, 401)
        self.assertIn(b"Invalid username or password", response.data)
        self.assertEqual(anon.get("/api/system/status").status_code, 401)

    def test_credentials_file_stores_hash_not_plaintext(self):
        self.app_module.load_credentials()
        path = os.environ["CREDENTIALS_PATH"]
        content = pathlib.Path(path).read_text(encoding="utf-8")
        self.assertIn('"username": "admin"', content)
        self.assertNotIn('"admin"\n', content.split("password_hash")[1])
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)

    def test_change_password_flow(self):
        client = self.app_module.app.test_client()
        client.post("/login", data={"username": "admin", "password": "admin"})
        with patch.object(self.app_module.time, "sleep"):
            bad = client.post("/api/auth/password", json={
                "current_password": "wrong", "new_password": "secret123", "confirm_password": "secret123"})
        self.assertEqual(bad.status_code, 400)
        short = client.post("/api/auth/password", json={
            "current_password": "admin", "new_password": "abc", "confirm_password": "abc"})
        self.assertEqual(short.status_code, 400)
        mismatch = client.post("/api/auth/password", json={
            "current_password": "admin", "new_password": "secret123", "confirm_password": "secret124"})
        self.assertEqual(mismatch.status_code, 400)

        ok = client.post("/api/auth/password", json={
            "current_password": "admin", "new_password": "secret123", "confirm_password": "secret123"})
        self.assertEqual(ok.status_code, 200)
        try:
            fresh = self.app_module.app.test_client()
            with patch.object(self.app_module.time, "sleep"):
                self.assertEqual(fresh.post("/login", data={"username": "admin", "password": "admin"}).status_code, 401)
            self.assertEqual(fresh.post("/login", data={"username": "admin", "password": "secret123"}).status_code, 302)
        finally:
            client.post("/api/auth/password", json={
                "current_password": "secret123", "new_password": "admin1", "confirm_password": "admin1"})
            os.remove(os.environ["CREDENTIALS_PATH"])  # back to default admin/admin

    def test_logout_ends_session(self):
        client = self.app_module.app.test_client()
        client.post("/login", data={"username": "admin", "password": "admin"})
        self.assertEqual(client.get("/api/system/status").status_code, 200)
        client.post("/logout")
        self.assertEqual(client.get("/api/system/status").status_code, 401)

    def test_index_has_change_password_next_to_service_buttons(self):
        html = self.client.get("/").data.decode("utf-8")
        service = html[html.index('class="service-btns"'):]
        service = service[: service.index("</section>")]
        self.assertLess(service.index("Restart Chrony"), service.index("Change Password"))
        self.assertLess(service.index("Change Password"), service.index("Reboot Pi"))

    def test_service_unit_has_persistent_state_dir(self):
        repo_root = pathlib.Path(__file__).resolve().parents[1]
        unit = (repo_root / "deploy" / "ntp-webgui.service").read_text(encoding="utf-8")
        self.assertIn("StateDirectory=ntp-webgui", unit)

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
