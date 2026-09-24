const express = require('express');
const path = require('path');
const { promisify } = require('util');
const { execFile } = require('child_process');

const execFileAsync = promisify(execFile);
const DEFAULT_TIMEOUT_MS = 5000;


function toBoolean(value) {
  if (typeof value === 'boolean') {
    return value;
  }

  if (typeof value === 'number') {
    return value !== 0;
  }

  if (typeof value === 'string') {
    const normalised = value.trim().toLowerCase();

    if (['true', 'yes', 'online', 'connected', '1'].includes(normalised)) {
      return true;
    }

    if (['false', 'no', 'offline', 'disconnected', '0'].includes(normalised)) {
      return false;
    }
  }

  return null;
}

// Parse the plain-text format emitted by the Python exporter:
//   NTP: <ip>
//   Time: <chrony-status>
//   Net: Online (<iface>) | Offline (<iface>)
//   CPU: <number>%
//   Temp: <number>°C
//   RAM: <number>%
function parsePlainTextPayload(text) {
  if (typeof text !== 'string') {
    throw new Error('Exporter response must be plain text.');
  }

  const fields = {};
  for (const line of text.split('\n')) {
    const colon = line.indexOf(':');
    if (colon === -1) continue;
    const key = line.slice(0, colon).trim().toLowerCase();
    const value = line.slice(colon + 1).trim();
    fields[key] = value;
  }

  // NTP: <ip>  (may be "Down")
  const ipAddress = fields['ntp'] || null;

  // Time: Stratum 1 | Active | Stopped
  const time = fields['time'] || null;

  // Net: Online (eth0) | Offline (None)
  const netValue = fields['net'] || '';
  const internetConnected = toBoolean(netValue.split(' ')[0]);

  // CPU: 12.3%
  const cpuRaw = fields['cpu'] || null;
  const cpu = cpuRaw != null ? parseFloat(cpuRaw) : null;

  // Temp: 45.2°C
  const tempRaw = fields['temp'] || null;
  const temp = tempRaw != null ? parseFloat(tempRaw) : null;

  // RAM: 34.1%
  const ramRaw = fields['ram'] || null;
  const ram = ramRaw != null ? parseFloat(ramRaw) : null;

  return {
    ipAddress: ipAddress?.toLowerCase() === 'down' ? null : ipAddress,
    internetConnected,
    time,
    cpu,
    temp,
    ram,
    raw: fields,
  };
}

function isValidCidrAddress(value) {
  if (typeof value !== 'string') {
    return false;
  }

  const match = value.trim().match(/^\d{1,3}(?:\.\d{1,3}){3}\/(?:\d|[12]\d|3[0-2])$/);

  if (!match) {
    return false;
  }

  const [ip] = value.trim().split('/');

  return ip.split('.').every((octet) => {
    const parsed = Number(octet);
    return Number.isInteger(parsed) && parsed >= 0 && parsed <= 255;
  });
}

async function applyNetworkAddress({ interfaceName, cidrAddress }) {
  await execFileAsync('ip', ['addr', 'replace', cidrAddress, 'dev', interfaceName]);

  // Attempt to bring the interface up. Only suppress errors that indicate the
  // interface is not yet physically present; all other errors (permissions,
  // typo in interface name, kernel errors) are re-thrown.
  try {
    await execFileAsync('ip', ['link', 'set', interfaceName, 'up']);
  } catch (error) {
    const stderr = (error.stderr ?? '').toString();
    if (!stderr.includes('Cannot find device')) {
      throw error;
    }
    // interface not present yet – address is still recorded for when it comes up
  }
}

function isValidInterfaceName(value) {
  return typeof value === 'string' && /^[a-zA-Z0-9][a-zA-Z0-9_-]{0,14}$/.test(value);
}

function createApp(options = {}) {
  const exporterUrl = options.exporterUrl ?? process.env.NTP_EXPORTER_URL ?? '';
  const networkInterface = options.networkInterface ?? process.env.NETWORK_INTERFACE ?? 'eth1';
  const fetchImpl = options.fetchImpl ?? global.fetch ?? null;
  const networkApplier = options.networkApplier ?? applyNetworkAddress;
  const timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS;

  if (exporterUrl && typeof fetchImpl !== 'function') {
    throw new Error('A fetch implementation is required.');
  }

  if (!isValidInterfaceName(networkInterface)) {
    throw new Error(
      'NETWORK_INTERFACE must start with a letter or number, use only letters, numbers, underscores, or hyphens, and be 15 characters or fewer.',
    );
  }

  const app = express();
  app.use(express.json());
  app.use(express.static(path.join(__dirname, '..', 'public')));

  app.get('/api/config', (_request, response) => {
    response.json({
      exporterConfigured: Boolean(exporterUrl),
      networkInterface,
    });
  });

  app.get('/api/status', async (_request, response) => {
    if (!exporterUrl) {
      response.status(500).json({
        error: 'NTP_EXPORTER_URL is not configured.',
      });
      return;
    }

    try {
      const exporterResponse = await fetchImpl(exporterUrl, {
        headers: {
          accept: 'application/json',
        },
        signal: AbortSignal.timeout(timeoutMs),
      });

      if (!exporterResponse.ok) {
        response.status(502).json({
          error: `Exporter request failed with status ${exporterResponse.status}.`,
        });
        return;
      }

      const payload = await exporterResponse.text();
      const status = parsePlainTextPayload(payload);

      response.json({
        networkInterface,
        ...status,
      });
    } catch (error) {
      response.status(502).json({
        error: error.message || 'Unable to read exporter data.',
      });
    }
  });

  app.post('/api/network', async (request, response) => {
    const cidrAddress = request.body?.ipAddress ?? request.body?.address ?? request.body?.cidr ?? '';

    if (!isValidCidrAddress(cidrAddress)) {
      response.status(400).json({
        error: 'Provide an IPv4 address in CIDR notation, for example 192.168.1.50/24.',
      });
      return;
    }

    try {
      await networkApplier({
        interfaceName: networkInterface,
        cidrAddress: cidrAddress.trim(),
      });

      response.json({
        message: `Updated ${networkInterface} to ${cidrAddress.trim()}.`,
      });
    } catch (error) {
      response.status(500).json({
        error: error.message || `Unable to update ${networkInterface}.`,
      });
    }
  });

  app.locals.config = {
    exporterUrl,
    networkInterface,
  };

  return app;
}

function startServer(options = {}) {
  const app = createApp(options);
  const port = Number(options.port ?? process.env.PORT ?? 3000);

  app.listen(port, () => {
    console.log(`NTP web UI listening on http://0.0.0.0:${port}`);
  });
}

module.exports = {
  applyNetworkAddress,
  createApp,
  isValidCidrAddress,
  isValidInterfaceName,
  parsePlainTextPayload,
  startServer,
};

if (require.main === module) {
  startServer();
}
