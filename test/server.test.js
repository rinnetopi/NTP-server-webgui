const test = require('node:test');
const assert = require('node:assert/strict');
const { createApp, isValidCidrAddress, parseCidrAddress, parsePlainTextPayload } = require('../src/server');

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

test('parsePlainTextPayload returns the expected dashboard fields', () => {
  const status = parsePlainTextPayload(
    'NTP: 192.168.1.20\nTime: Stratum 1\nNet: Online (eth0)\nCPU: 12.5%\nTemp: 45.2°C\nRAM: 34.1%',
  );

  assert.deepEqual(status, {
    ipAddress: '192.168.1.20',
    internetConnected: true,
    time: 'Stratum 1',
    cpu: 12.5,
    temp: 45.2,
    ram: 34.1,
    raw: {
      ntp: '192.168.1.20',
      time: 'Stratum 1',
      net: 'Online (eth0)',
      cpu: '12.5%',
      temp: '45.2°C',
      ram: '34.1%',
    },
  });
});

test('parsePlainTextPayload maps "Down" IP to null', () => {
  const status = parsePlainTextPayload(
    'NTP: Down\nTime: Stopped\nNet: Offline (None)\nCPU: 0.0%\nTemp: 40.0°C\nRAM: 20.0%',
  );

  assert.equal(status.ipAddress, null);
  assert.equal(status.internetConnected, false);
});

test('parseCidrAddress computes the correct chrony allow and broadcast values', () => {
  const network = parseCidrAddress('192.168.1.169/24');

  assert.deepEqual(network, {
    network: '192.168.1.0/24',
    broadcast: '192.168.1.255',
  });
});

test('GET /api/status accepts JSON exporter payloads and normalises them', async (t) => {
  const app = createApp({
    exporterUrl: 'http://exporter.local/status',
    fetchImpl: async () => ({
      ok: true,
      async text() {
        return JSON.stringify({
          ipAddress: '192.168.1.10',
          internetConnected: true,
          time: 'Stratum 2',
          cpu: '45.5%',
          temp: '56.0°C',
          ram: '67.8%',
        });
      },
    }),
  });

  const { server, url } = await startTestServer(app);
  t.after(() => server.close());

  const response = await fetch(`${url}/api/status`);
  const body = await response.json();

  assert.equal(response.status, 200);
  assert.equal(body.ipAddress, '192.168.1.10');
  assert.equal(body.internetConnected, true);
  assert.equal(body.time, 'Stratum 2');
  assert.equal(body.cpu, 45.5);
  assert.equal(body.temp, 56.0);
  assert.equal(body.ram, 67.8);
});

test('isValidCidrAddress accepts valid IPv4 CIDR strings', () => {
  assert.equal(isValidCidrAddress('192.168.1.10/24'), true);
  assert.equal(isValidCidrAddress('10.0.0.5/8'), true);
  assert.equal(isValidCidrAddress('10.0.0.500/24'), false);
  assert.equal(isValidCidrAddress('10.0.0.10'), false);
});

test('createApp rejects an invalid network interface name', () => {
  assert.throws(
    () =>
      createApp({
        networkInterface: '--bad-interface',
      }),
    /NETWORK_INTERFACE must start with a letter or number/,
  );
});

test('createApp rejects a network interface name longer than 15 characters', () => {
  assert.throws(
    () =>
      createApp({
        networkInterface: 'abcdefghijklmnop',
      }),
    /15 characters or fewer/,
  );
});

test('GET /api/status returns normalised exporter data', async (t) => {
  const app = createApp({
    exporterUrl: 'http://exporter.local/status',
    fetchImpl: async () => ({
      ok: true,
      async text() {
        return 'NTP: 172.16.0.3\nTime: Stratum 2\nNet: Offline (None)\nCPU: 5.0%\nTemp: 42.0°C\nRAM: 55.0%';
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
  assert.equal(body.time, 'Stratum 2');
  assert.equal(body.cpu, 5.0);
  assert.equal(body.temp, 42.0);
  assert.equal(body.ram, 55.0);
});

test('GET /api/status returns 500 when the exporter URL is not configured', async (t) => {
  const app = createApp({
    exporterUrl: '',
  });

  const { server, url } = await startTestServer(app);
  t.after(() => server.close());

  const response = await fetch(`${url}/api/status`);
  const body = await response.json();

  assert.equal(response.status, 500);
  assert.equal(body.error, 'NTP_EXPORTER_URL is not configured.');
});

test('GET /api/status returns 502 when the exporter responds with an error', async (t) => {
  const app = createApp({
    exporterUrl: 'http://exporter.local/status',
    fetchImpl: async () => ({
      ok: false,
      status: 503,
    }),
  });

  const { server, url } = await startTestServer(app);
  t.after(() => server.close());

  const response = await fetch(`${url}/api/status`);
  const body = await response.json();

  assert.equal(response.status, 502);
  assert.equal(body.error, 'Exporter request failed with status 503.');
});

test('POST /api/network validates the request body and calls the applier', async (t) => {
  const calls = [];
  const app = createApp({
    exporterUrl: 'http://exporter.local/status',
    fetchImpl: async () => ({
      ok: true,
      async text() {
        return 'NTP: 192.168.50.15\nTime: Stopped\nNet: Offline (None)\nCPU: 0.0%\nTemp: 40.0°C\nRAM: 20.0%';
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
      async text() {
        return 'NTP: 192.168.50.15\nTime: Stopped\nNet: Offline (None)\nCPU: 0.0%\nTemp: 40.0°C\nRAM: 20.0%';
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
