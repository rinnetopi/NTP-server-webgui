# NTP-server-webgui

Simple web UI for a Raspberry Pi NTP server.

## Features

- Shows the exporter IP address, internet status, and current time
- Lets you refresh the latest exporter values from the browser
- Lets you update the `eth1` address with an IPv4 CIDR value such as `192.168.1.50/24` — even when the interface is currently down

## Configuration

Set these environment variables before starting the server:

| Variable | Default | Description |
|---|---|---|
| `NTP_EXPORTER_URL` | *(required)* | URL of your local exporter, e.g. `http://localhost:8080` |
| `PORT` | `3000` | HTTP port the web UI listens on |
| `NETWORK_INTERFACE` | `eth1` | Network adapter to manage |

The exporter may return either plain text or JSON. The app normalizes both to the same dashboard fields. The Python exporter used by this project emits plain text lines such as `NTP: ...`, `Time: ...`, `Net: ...`, `CPU: ...`, `Temp: ...`, and `RAM: ...`.

## Run locally

```bash
npm install
NTP_EXPORTER_URL=http://127.0.0.1:8080 npm start
```

Then open `http://127.0.0.1:3000`.

## Run as a systemd service (auto-start at boot)

1. Install the application:

   ```bash
   sudo cp -r . /opt/ntp-server-webgui
   cd /opt/ntp-server-webgui && sudo npm install --omit=dev
   ```

2. Copy and edit the service unit:

   ```bash
   sudo cp deploy/ntp-webgui.service /etc/systemd/system/
   sudo nano /etc/systemd/system/ntp-webgui.service
   # Set NTP_EXPORTER_URL, PORT, and NETWORK_INTERFACE to match your setup
   ```

3. Enable and start the service:

   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable --now ntp-webgui
   ```

4. Check the status:

   ```bash
   sudo systemctl status ntp-webgui
   ```

The service unit file is at [`deploy/ntp-webgui.service`](deploy/ntp-webgui.service).

## Notes

- Updating the network address uses the `ip` command, so the process must have permission to manage the selected network interface (e.g. run as root or grant `CAP_NET_ADMIN`).
- The address update sets the address in the running kernel. If the interface is down at the time, the change still succeeds and takes effect when the interface comes up. For persistence across reboots, update `/etc/network/interfaces` or your system's network configuration.

## Test

```bash
npm test
```