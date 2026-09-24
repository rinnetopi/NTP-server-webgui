const express = require('express');
const path = require('path');
const { promisify } = require('util');
const { execFile } = require('child_process');

const execFileAsync = promisify(execFile);
const DEFAULT_TIMEOUT_MS = 5000;

function firstDefined(payload, paths) {
  for (const pathParts of paths) {
    let current = payload;

    for (const part of pathParts) {
      if (current == null || typeof current !== 'object' || !(part in current)) {
        current = undefined;
        break;
      }

      current = current[part];
    }

    if (current !== undefined && current !== null && current !== '') {
      return current;
    }
  }

  return null;
}

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

function normalizeExporterPayload(payload) {
  if (!payload || typeof payload !== 'object' || Array.isArray(payload)) {
    throw new Error('Exporter response must be a JSON object.');
  }

  const ipAddress = firstDefined(payload, [
    ['ipAddress'],
    ['ip_address'],
    ['ip'],
    ['network', 'ipAddress'],
    ['network', 'ip_address'],
    ['network', 'ip'],
    ['eth1', 'ipAddress'],
    ['eth1', 'ip_address'],
    ['eth1', 'ip'],
  ]);

  const internetValue = firstDefined(payload, [
    ['internetConnected'],
    ['internet_connected'],
    ['isConnectedToInternet'],
    ['is_connected_to_internet'],
    ['connectedToInternet'],
    ['connected_to_internet'],
    ['internet'],
    ['online'],
    ['network', 'internetConnected'],
    ['network', 'online'],
    ['eth1', 'internetConnected'],
    ['eth1', 'online'],
  ]);

  const time = firstDefined(payload, [
    ['time'],
    ['currentTime'],
    ['current_time'],
    ['ntpTime'],
    ['ntp_time'],
    ['status', 'time'],
    ['ntp', 'time'],
  ]);

  return {
    ipAddress,
    internetConnected: toBoolean(internetValue),
    time: time == null ? null : String(time),
    raw: payload,
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

      const payload = await exporterResponse.json();
      const status = normalizeExporterPayload(payload);

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
  normalizeExporterPayload,
  startServer,
};

if (require.main === module) {
  startServer();
}
