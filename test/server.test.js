const test = require('node:test');
const assert = require('node:assert/strict');
const { createApp, isValidCidrAddress, normalizeExporterPayload } = require('../src/server');

async function startTestServer(app) {
  return await new Promise((resolve) => {
    const server = app.listen(0, () => {
      const { port } = server.address();
      resolve({
        server,
        url: `http://127.0.0.1:${port}`,
      });
    });
  });
}

test('normalizeExporterPayload returns the expected dashboard fields', () => {
  const status = normalizeExporterPayload({
    network: {
      ip: '192.168.1.20',
      online: 'yes',
    },
    ntp: {
      time: '2026-09-22T15:00:00Z',
    },
  });

  assert.deepEqual(status, {
    ipAddress: '192.168.1.20',
    internetConnected: true,
    time: '2026-09-22T15:00:00Z',
    raw: {
      network: {
        ip: '192.168.1.20',
        online: 'yes',
      },
      ntp: {
        time: '2026-09-22T15:00:00Z',
      },
    },
  });
});

test('isValidCidrAddress accepts valid IPv4 CIDR strings', () => {
  assert.equal(isValidCidrAddress('192.168.1.10/24'), true);
  assert.equal(isValidCidrAddress('10.0.0.5/8'), true);
  assert.equal(isValidCidrAddress('10.0.0.500/24'), false);
  assert.equal(isValidCidrAddress('10.0.0.10'), false);
});

test('GET /api/status returns normalised exporter data', async (t) => {
  const app = createApp({
    exporterUrl: 'http://exporter.local/status',
    fetchImpl: async () => ({
      ok: true,
      async json() {
        return {
          ipAddress: '172.16.0.3',
          internetConnected: false,
          time: '2026-09-22T15:12:00Z',
        };
      },
    }),
  });

  const { server, url } = await startTestServer(app);
  t.after(() => server.close());

  const response = await fetch(`${url}/api/status`);
  const body = await response.json();

  assert.equal(response.status, 200);
  assert.equal(body.ipAddress, '172.16.0.3');
  assert.equal(body.internetConnected, false);
  assert.equal(body.time, '2026-09-22T15:12:00Z');
});

test('POST /api/network validates the request body and calls the applier', async (t) => {
  const calls = [];
  const app = createApp({
    exporterUrl: 'http://exporter.local/status',
    fetchImpl: async () => ({
      ok: true,
      async json() {
        return {};
      },
    }),
    networkApplier: async (payload) => {
      calls.push(payload);
    },
  });

  const { server, url } = await startTestServer(app);
  t.after(() => server.close());

  const response = await fetch(`${url}/api/network`, {
    method: 'POST',
    headers: {
      'content-type': 'application/json',
    },
    body: JSON.stringify({ ipAddress: '192.168.50.15/24' }),
  });
  const body = await response.json();

  assert.equal(response.status, 200);
  assert.equal(body.message, 'Updated eth1 to 192.168.50.15/24.');
  assert.deepEqual(calls, [
    {
      interfaceName: 'eth1',
      cidrAddress: '192.168.50.15/24',
    },
  ]);
});

test('POST /api/network rejects an invalid address', async (t) => {
  const app = createApp({
    exporterUrl: 'http://exporter.local/status',
    fetchImpl: async () => ({
      ok: true,
      async json() {
        return {};
      },
    }),
  });

  const { server, url } = await startTestServer(app);
  t.after(() => server.close());

  const response = await fetch(`${url}/api/network`, {
    method: 'POST',
    headers: {
      'content-type': 'application/json',
    },
    body: JSON.stringify({ ipAddress: 'bad value' }),
  });
  const body = await response.json();

  assert.equal(response.status, 400);
  assert.match(body.error, /CIDR notation/);
});
