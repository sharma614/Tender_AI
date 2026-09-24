import './style.css';

const API_BASE = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000';

const app = document.querySelector('#app');

app.innerHTML = `
  <main class="container">
    <section class="card">
      <p class="eyebrow">TenderAI</p>
      <h1>AI tender analysis</h1>
      <p class="subtitle">
        Frontend runs on Vite. The real app stays on AWS.
      </p>

      <div class="status-row">
        <span class="label">API base:</span>
        <code>${API_BASE}</code>
      </div>

      <div class="status-box" id="health-box">Checking backend health…</div>

      <form id="upload-form" class="upload-form">
        <label for="file-input">Upload a PDF tender</label>
        <input id="file-input" type="file" accept="application/pdf" />
        <button type="submit">Analyze tender</button>
      </form>

      <div id="result"></div>
    </section>
  </main>
`;

async function checkHealth() {
  const healthBox = document.querySelector('#health-box');

  try {
    const response = await fetch(`${API_BASE}/health`);
    const data = await response.json();
    healthBox.textContent = response.ok
      ? `Backend healthy: ${data.status}`
      : `Backend not healthy: ${response.status}`;
    healthBox.classList.toggle('ok', response.ok);
  } catch (error) {
    healthBox.textContent = `Backend unavailable: ${error.message}`;
    healthBox.classList.add('error');
  }
}

async function loadTenders() {
  try {
    const response = await fetch(`${API_BASE}/tenders`);
    if (!response.ok) throw new Error(`Status ${response.status}`);
    const tenders = await response.json();
    const result = document.querySelector('#result');
    result.innerHTML = tenderListHtml(tenders);
  } catch (error) {
    const result = document.querySelector('#result');
    result.innerHTML = `<p class="error-text">Unable to load tenders: ${error.message}</p>`;
  }
}

function tenderListHtml(tenders) {
  if (!tenders.length) {
    return '<p>No tenders yet.</p>';
  }

  const items = tenders
    .slice(0, 5)
    .map(
      (tender) => `
        <li>
          <strong>${tender.title || 'Untitled tender'}</strong>
          <span>id: ${tender.id}</span>
        </li>
      `,
    )
    .join('');

  return `
    <h2>Recent tenders</h2>
    <ul class="tender-list">${items}</ul>
  `;
}

const form = document.querySelector('#upload-form');
form.addEventListener('submit', async (event) => {
  event.preventDefault();
  const fileInput = document.querySelector('#file-input');
  const file = fileInput.files[0];

  if (!file) {
    document.querySelector('#result').innerHTML = '<p>Please choose a PDF first.</p>';
    return;
  }

  const formData = new FormData();
  formData.append('file', file);

  const result = document.querySelector('#result');
  result.innerHTML = '<p>Uploading and analyzing…</p>';

  try {
    const response = await fetch(`${API_BASE}/upload`, {
      method: 'POST',
      body: formData,
    });

    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.detail || 'Upload failed');
    }

    result.innerHTML = `
      <p class="success-text">Job created successfully.</p>
      <pre>${JSON.stringify(data, null, 2)}</pre>
    `;
  } catch (error) {
    result.innerHTML = `<p class="error-text">${error.message}</p>`;
  }
});

checkHealth();
loadTenders();
