# NTP-server-webgui

Simple web UI for a Raspberry Pi NTP server.

## Features

- Shows the exporter IP address, internet status, and current time
- Lets you refresh the latest exporter values from the browser
- Lets you update the `eth1` address with an IPv4 CIDR value such as `192.168.1.50/24`

## Configuration

Set these environment variables before starting the server:

- `NTP_EXPORTER_URL` - URL of your local JSON exporter
- `PORT` - optional HTTP port, defaults to `3000`
- `NETWORK_INTERFACE` - optional network adapter name, defaults to `eth1`

## Run locally

```bash
npm install
NTP_EXPORTER_URL=http://127.0.0.1:9090/status npm start
```

Then open `http://127.0.0.1:3000`.

## Notes

- The exporter endpoint must return a JSON object. The app looks for common keys such as `ipAddress`, `internetConnected`, and `time`.
- Updating the network address uses the `ip` command, so the process must have permission to manage the selected network interface.
- The address update changes the active address. If you also need persistence across reboots, update your Raspberry Pi network configuration separately.

## Test

```bash
npm test
```