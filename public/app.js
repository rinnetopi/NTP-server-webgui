const ipAddressElement = document.getElementById('ipAddress');
const internetConnectedElement = document.getElementById('internetConnected');
const timeValueElement = document.getElementById('timeValue');
const rawPayloadElement = document.getElementById('rawPayload');
const statusMessageElement = document.getElementById('statusMessage');
const formMessageElement = document.getElementById('formMessage');
const networkForm = document.getElementById('networkForm');
const refreshButton = document.getElementById('refreshButton');
const interfaceLabel = document.getElementById('interfaceLabel');

function setMessage(element, message, isError = false) {
  element.textContent = message;
  element.style.color = isError ? '#fca5a5' : '#93c5fd';
}

function renderStatus(status) {
  ipAddressElement.textContent = status.ipAddress ?? 'Unavailable';
  internetConnectedElement.textContent =
    status.internetConnected == null ? 'Unavailable' : status.internetConnected ? 'Connected' : 'Offline';
  timeValueElement.textContent = status.time ?? 'Unavailable';
  rawPayloadElement.textContent = JSON.stringify(status.raw ?? {}, null, 2);
}

async function loadConfig() {
  const response = await fetch('/api/config');
  const config = await response.json();
  interfaceLabel.textContent = `${config.networkInterface} address format: 192.168.1.50/24`;
}

async function loadStatus() {
  setMessage(statusMessageElement, 'Loading status...');

  try {
    const response = await fetch('/api/status');
    const body = await response.json();

    if (!response.ok) {
      throw new Error(body.error || 'Unable to load exporter data.');
    }

    renderStatus(body);
    setMessage(statusMessageElement, `Last refresh: ${new Date().toLocaleTimeString()}`);
  } catch (error) {
    renderStatus({});
    setMessage(statusMessageElement, error.message, true);
  }
}

networkForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  const formData = new FormData(networkForm);
  const ipAddress = String(formData.get('ipAddress') || '').trim();

  setMessage(formMessageElement, 'Applying address...');

  try {
    const response = await fetch('/api/network', {
      method: 'POST',
      headers: {
        'content-type': 'application/json',
      },
      body: JSON.stringify({ ipAddress }),
    });
    const body = await response.json();

    if (!response.ok) {
      throw new Error(body.error || 'Unable to apply the new address.');
    }

    setMessage(formMessageElement, body.message);
    networkForm.reset();
    await loadStatus();
  } catch (error) {
    setMessage(formMessageElement, error.message, true);
  }
});

refreshButton.addEventListener('click', () => {
  loadStatus();
});

Promise.all([loadConfig(), loadStatus()]).catch((error) => {
  setMessage(statusMessageElement, error.message, true);
});
