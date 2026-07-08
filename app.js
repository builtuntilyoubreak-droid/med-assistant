const state = {
  myClinic: 'Clinic A',
  activeTab: 'dashboard',
  batches: [],
  suggestedActions: [],
  requests: [],
  history: [],
  selectedForecastItem: '',
  isLoading: true,
  isApiOnline: false,
  newStock: { item: '', batch_id: '', quantity: '', expiry_date: '' },
  submittingActionId: null,
  respondingRequestId: null
};

// Toast notification manager
function addToast(message, type = 'info') {
  const container = document.getElementById('toast-container');
  if (!container) return;
  
  const toast = document.createElement('div');
  toast.className = `toast ${type}`;
  toast.innerHTML = `
    <div class="toast-icon">
      ${type === 'success' ? '✓' : 'ℹ'}
    </div>
    <div class="toast-message">${message}</div>
  `;
  
  container.appendChild(toast);
  setTimeout(() => {
    toast.style.opacity = '0';
    toast.style.transform = 'translateY(10px)';
    setTimeout(() => toast.remove(), 300);
  }, 4000);
}

// Fetch dataset from API
async function fetchData() {
  state.isLoading = true;
  render();
  try {
    const healthRes = await fetch('/health').catch(() => null);
    if (!healthRes || !healthRes.ok) {
      state.isApiOnline = false;
      state.isLoading = false;
      addToast('Cannot connect to the clinic network backend server.', 'error');
      render();
      return;
    }
    state.isApiOnline = true;

    const [batchesRes, actionsRes, reqsRes, histRes] = await Promise.all([
      fetch('/stock/batches'),
      fetch(`/actions/suggested?clinic=${state.myClinic}`),
      fetch(`/requests?clinic=${state.myClinic}`),
      fetch('/transactions/history')
    ]);

    state.batches = await batchesRes.json();
    state.suggestedActions = await actionsRes.json();
    state.requests = await reqsRes.json();
    state.history = await histRes.json();

    // Set default selected forecast item
    const myItems = [...new Set(state.batches.filter(b => b.clinic === state.myClinic).map(b => b.item))];
    if (myItems.length > 0 && (!state.selectedForecastItem || !myItems.includes(state.selectedForecastItem))) {
      state.selectedForecastItem = myItems[0];
    }
  } catch (error) {
    console.error(error);
    addToast('Error syncing clinic data.', 'error');
  } finally {
    state.isLoading = false;
    render();
  }
}

// Global actions exposed to HTML
window.changeClinic = function(name) {
  state.myClinic = name;
  fetchData();
};

window.changeTab = function(tabName) {
  state.activeTab = tabName;
  render();
};

window.changeForecastItem = function(item) {
  state.selectedForecastItem = item;
  render();
};

window.sendNetworkRequest = async function(idx) {
  state.submittingActionId = idx;
  render();
  const action = state.suggestedActions[idx];
  try {
    const response = await fetch('/requests/create', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        type: action.type,
        item: action.item,
        from_clinic: action.from_clinic,
        to_clinic: action.to_clinic,
        quantity: parseInt(action.quantity),
        batch_row_id: action.batch_row_id,
        reason: action.reason,
        penalty_score: action.penalty_score
      })
    });

    if (response.ok) {
      addToast(`Dispatched stock ${action.type.toLowerCase()} request for ${action.item} successfully.`, 'success');
      await fetchData();
    } else {
      const res = await response.json();
      addToast(`Request failed: ${res.detail || 'Server error'}`, 'error');
    }
  } catch (error) {
    addToast('Network error while dispatching request.', 'error');
  } finally {
    state.submittingActionId = null;
    render();
  }
};

window.respondToRequest = async function(reqId, isApprove) {
  state.respondingRequestId = reqId;
  render();
  const action = isApprove ? 'APPROVED' : 'REJECTED';
  try {
    const response = await fetch('/requests/respond', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        request_id: reqId,
        action: action
      })
    });

    if (response.ok) {
      addToast(isApprove ? 'Request approved. Stock has been transferred.' : 'Request declined.', 'info');
      await fetchData();
    } else {
      const res = await response.json();
      addToast(`Failed to respond: ${res.detail || 'Server error'}`, 'error');
    }
  } catch (error) {
    addToast('Network error while responding.', 'error');
  } finally {
    state.respondingRequestId = null;
    render();
  }
};

window.showAddStockModal = function(show) {
  const modal = document.getElementById('add-stock-modal');
  if (modal) {
    modal.style.display = show ? 'flex' : 'none';
  }
};

window.submitAddStock = async function(e) {
  e.preventDefault();
  const item = document.getElementById('modal-item').value;
  const batch_id = document.getElementById('modal-batch-id').value;
  const quantity = document.getElementById('modal-qty').value;
  const expiry_date = document.getElementById('modal-expiry').value;

  if (!item || !batch_id || !quantity || !expiry_date) {
    addToast('All fields are required.', 'error');
    return;
  }

  try {
    const response = await fetch('/stock/add', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        clinic: state.myClinic,
        item: item,
        batch_id: batch_id,
        quantity: parseInt(quantity),
        expiry_date: expiry_date
      })
    });

    if (response.ok) {
      addToast(`Intake recorded: batch ${batch_id} added to local inventory.`, 'success');
      window.showAddStockModal(false);
      await fetchData();
    } else {
      const res = await response.json();
      addToast(`Intake failed: ${res.detail || 'Unknown error'}`, 'error');
    }
  } catch (error) {
    addToast('Network error recording intake.', 'error');
  }
};

// UI Rendering Engine
function render() {
  renderSidebar();
  renderHeader();
  renderAlertsBanner();
  
  const content = document.getElementById('tab-content');
  if (!content) return;

  if (state.isLoading) {
    content.innerHTML = `
      <div class="loader-container">
        <div class="loader"></div>
        <div>Loading clinic dataset...</div>
      </div>
    `;
    return;
  }

  switch (state.activeTab) {
    case 'dashboard':
      renderDashboard(content);
      break;
    case 'stock':
      renderStock(content);
      break;
    case 'forecasting':
      renderForecasting(content);
      break;
    case 'network':
      renderNetwork(content);
      break;
  }
}

function renderSidebar() {
  const container = document.getElementById('sidebar-container');
  if (!container) return;

  const clinics = CLINIC_LIST.map(c => `
    <option value="${c}" ${state.myClinic === c ? 'selected' : ''}>${c}</option>
  `).join('');

  container.innerHTML = `
    <div class="logo-container">
      <div class="logo-icon">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2.5">
          <rect x="2" y="7" width="20" height="14" rx="2" ry="2" />
          <path d="M16 21V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v16" />
        </svg>
      </div>
      <span class="logo-text">CareShare</span>
      <span class="logo-badge">Clinic</span>
    </div>

    <div class="clinic-context-box">
      <span class="context-label">Local Clinic Context</span>
      <select class="context-select" onchange="changeClinic(this.value)">
        ${clinics}
      </select>
    </div>

    <nav>
      <ul class="nav-links">
        <li>
          <button onclick="changeTab('dashboard')" class="nav-item ${state.activeTab === 'dashboard' ? 'active' : ''}">
            <svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <rect x="3" y="3" width="7" height="9" rx="1" /><rect x="14" y="3" width="7" height="5" rx="1" />
              <rect x="14" y="12" width="7" height="9" rx="1" /><rect x="3" y="16" width="7" height="5" rx="1" />
            </svg>
            Clinic Dashboard
          </button>
        </li>
        <li>
          <button onclick="changeTab('stock')" class="nav-item ${state.activeTab === 'stock' ? 'active' : ''}">
            <svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M12 2v20M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6" />
            </svg>
            My Stock Inventory
          </button>
        </li>
        <li>
          <button onclick="changeTab('forecasting')" class="nav-item ${state.activeTab === 'forecasting' ? 'active' : ''}">
            <svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <line x1="18" y1="20" x2="18" y2="10" /><line x1="12" y1="20" x2="12" y2="4" /><line x1="6" y1="20" x2="6" y2="14" />
            </svg>
            Demand Forecasting
          </button>
        </li>
        <li>
          <button onclick="changeTab('network')" class="nav-item ${state.activeTab === 'network' ? 'active' : ''}">
            <svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M16 16v1a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2h11a2 2 0 0 1 2 2v1" />
              <path d="M18 8h4a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2h-4" /><path d="M12 22v-4" /><circle cx="12" cy="14" r="4" />
            </svg>
            Network Stock
          </button>
        </li>
      </ul>
    </nav>

    <div class="sidebar-footer">
      <div class="api-status">
        <span class="status-dot ${state.isApiOnline ? 'online' : ''}"></span>
        <span style="color: #94a3b8; font-size: 0.78rem;">
          ${state.isApiOnline ? 'Network Connected' : 'Connection Error'}
        </span>
      </div>
    </div>
  `;
}

function renderHeader() {
  const container = document.getElementById('header-container');
  if (!container) return;

  let title = '';
  let subtitle = '';

  switch (state.activeTab) {
    case 'dashboard':
      title = `Dashboard Overview — ${state.myClinic}`;
      subtitle = 'Manage alerts, send enquiries, and handle incoming/outgoing transfers.';
      break;
    case 'stock':
      title = `Local Stock Inventory — ${state.myClinic}`;
      subtitle = 'Detailed overview of local batches, expiry dates, and calculated stock penalties.';
      break;
    case 'forecasting':
      title = `Usage Forecasting & Analytics — ${state.myClinic}`;
      subtitle = 'Linear regression and season-adjusted upcoming demand forecasts.';
      break;
    case 'network':
      title = 'Clinic Network stock levels';
      subtitle = 'Observe stock availability at other clinics to request matching redistribution.';
      break;
  }

  container.innerHTML = `
    <div>
      <h1 class="header-title">${title}</h1>
      <p class="header-subtitle">${subtitle}</p>
    </div>
    <button onclick="showAddStockModal(true)" class="btn btn-primary">
      + Record Local Delivery
    </button>
  `;
}

function renderAlertsBanner() {
  const container = document.getElementById('alerts-banner-container');
  if (!container) return;

  if (!state.isApiOnline && !state.isLoading) {
    container.innerHTML = `
      <div class="banner" style="background-color: var(--danger-bg); border-color: var(--danger-border); color: var(--danger);">
        <strong>Server Offline</strong> — The application is currently disconnected from the FastAPI server. Please check your backend connection.
      </div>
    `;
  } else {
    container.innerHTML = '';
  }
}

function renderDashboard(target) {
  const myBatches = state.batches.filter(b => b.clinic === state.myClinic);
  const lowStockCount = myBatches.filter(b => b.days_of_supply < 7).length;
  const expiryRiskCount = myBatches.filter(b => b.days_to_expiry <= 14).length;
  const holdingPenaltyCount = myBatches.filter(b => b.holding_penalty > 0).length;

  const incomingRequests = state.requests.filter(r => r.to_clinic === state.myClinic && r.status === 'PENDING');
  const outgoingRequests = state.requests.filter(r => r.from_clinic === state.myClinic);

  // Suggested actions markup
  let suggestedMarkup = '';
  if (state.suggestedActions.length === 0) {
    suggestedMarkup = `
      <div class="empty-state" style="padding: 2rem; border: none;">
        ✓
        <div style="font-weight: 600; color: var(--text-primary); margin-top: 0.5rem;">No Actions Required</div>
        <p style="font-size: 0.8rem; color: var(--text-secondary);">Local stock levels are currently balanced.</p>
      </div>
    `;
  } else {
    suggestedMarkup = `
      <div class="action-list">
        ${state.suggestedActions.map((action, idx) => `
          <div class="action-row">
            <div>
              <div class="action-desc">${action.description}</div>
              <div class="action-badge-row">
                <span class="badge ${action.type === 'ENQUIRY' ? 'badge-danger' : 'badge-warning'}">
                  ${action.type === 'ENQUIRY' ? 'Stock Shortage' : 'Redistribution'}
                </span>
                ${action.penalty_score > 0 ? `
                  <span class="badge badge-neutral" style="font-size: 0.65rem;">
                    Holding Penalty: ${action.penalty_score} pts
                  </span>
                ` : ''}
              </div>
            </div>
            <button onclick="sendNetworkRequest(${idx})" class="btn btn-primary" style="padding: 0.45rem 0.85rem;" ${state.submittingActionId === idx ? 'disabled' : ''}>
              ${state.submittingActionId === idx ? 'Sending...' : 'Send Request'}
            </button>
          </div>
        `).join('')}
      </div>
    `;
  }

  // Incoming requests markup
  let incomingMarkup = '';
  if (incomingRequests.length === 0) {
    incomingMarkup = `
      <div class="empty-state" style="padding: 2rem; border: none;">
        ✉
        <div style="font-weight: 600; color: var(--text-primary); margin-top: 0.5rem;">Inbox Empty</div>
        <p style="font-size: 0.8rem; color: var(--text-secondary);">No pending requests from other clinics.</p>
      </div>
    `;
  } else {
    incomingMarkup = `
      <div class="action-list">
        ${incomingRequests.map(req => `
          <div class="action-row" style="border-left: 4px solid var(--primary);">
            <div>
              <div style="font-weight: 600; font-size: 0.9rem;">
                ${req.from_clinic} asks for ${req.quantity} ${req.item}
              </div>
              <div style="font-size: 0.8rem; color: var(--text-secondary); margin-top: 0.15rem;">
                Reason: ${req.reason.replace(/_/g, ' ')}
              </div>
              <div class="action-badge-row">
                <span class="badge ${req.type === 'ENQUIRY' ? 'badge-danger' : 'badge-warning'}">
                  ${req.type}
                </span>
              </div>
            </div>
            <div style="display: flex; gap: 0.5rem;">
              <button onclick="respondToRequest(${req.id}, true)" class="btn btn-success" style="padding: 0.4rem 0.75rem; font-size: 0.78rem;" ${state.respondingRequestId === req.id ? 'disabled' : ''}>
                Approve
              </button>
              <button onclick="respondToRequest(${req.id}, false)" class="btn btn-secondary" style="padding: 0.4rem 0.75rem; font-size: 0.78rem;" ${state.respondingRequestId === req.id ? 'disabled' : ''}>
                Decline
              </button>
            </div>
          </div>
        `).join('')}
      </div>
    `;
  }

  // Outgoing requests log table
  let outgoingMarkup = '';
  if (outgoingRequests.length === 0) {
    outgoingMarkup = `
      <div style="text-align: center; color: var(--text-secondary); padding: 1.5rem; font-size: 0.85rem;">
        No requests have been sent by ${state.myClinic} yet.
      </div>
    `;
  } else {
    outgoingMarkup = `
      <div class="table-container">
        <table class="data-table">
          <thead>
            <tr>
              <th>Item</th>
              <th>Quantity</th>
              <th>Target Clinic</th>
              <th>Request Type</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            ${outgoingRequests.map(r => `
              <tr>
                <td style="font-weight: 600;">${r.item}</td>
                <td>${r.quantity} units</td>
                <td>${r.to_clinic}</td>
                <td>
                  <span class="badge ${r.type === 'ENQUIRY' ? 'badge-danger' : 'badge-warning'}">
                    ${r.type}
                  </span>
                </td>
                <td>
                  <span class="badge ${
                    r.status === 'PENDING' ? 'badge-neutral' :
                    r.status === 'APPROVED' ? 'badge-success' : 'badge-danger'
                  }">
                    ${r.status}
                  </span>
                </td>
              </tr>
            `).join('')}
          </tbody>
        </table>
      </div>
    `;
  }

  target.innerHTML = `
    <!-- Stats metric cards -->
    <section class="stats-grid">
      <div class="stat-card">
        <div class="stat-icon">📦</div>
        <div class="stat-info">
          <span class="stat-label">Stocked Items</span>
          <span class="stat-value">${myBatches.length}</span>
        </div>
      </div>
      <div class="stat-card alert">
        <div class="stat-icon">⚠</div>
        <div class="stat-info">
          <span class="stat-label">Low Stock Alerts</span>
          <span class="stat-value" style="color: var(--danger);">${lowStockCount}</span>
        </div>
      </div>
      <div class="stat-card warning">
        <div class="stat-icon">⏰</div>
        <div class="stat-info">
          <span class="stat-label">Expiry Risk Batches</span>
          <span class="stat-value" style="color: var(--warning);">${expiryRiskCount}</span>
        </div>
      </div>
      <div class="stat-card success">
        <div class="stat-icon">⚖</div>
        <div class="stat-info">
          <span class="stat-label">Holding Penalties</span>
          <span class="stat-value" style="color: var(--success);">${holdingPenaltyCount}</span>
        </div>
      </div>
    </section>

    <!-- Suggested & Inbox division -->
    <div class="columns-2">
      <div class="panel" style="height: fit-content;">
        <div class="panel-header">
          <h2 class="panel-title">Suggested Network Requests</h2>
          <span class="badge badge-info">${state.suggestedActions.length} Pending</span>
        </div>
        ${suggestedMarkup}
      </div>

      <div class="panel" style="height: fit-content;">
        <div class="panel-header">
          <h2 class="panel-title">Received Requests Box</h2>
          <span class="badge badge-neutral">${incomingRequests.length} Pending</span>
        </div>
        ${incomingMarkup}
      </div>
    </div>

    <!-- Outgoing request log -->
    <div class="panel">
      <div class="panel-header">
        <h2 class="panel-title">Outgoing Network Requests Status</h2>
      </div>
      ${outgoingMarkup}
    </div>
  `;
}

function renderStock(target) {
  const myBatches = state.batches.filter(b => b.clinic === state.myClinic);

  let tableMarkup = '';
  if (myBatches.length === 0) {
    tableMarkup = `
      <div class="empty-state">
        <div class="empty-title">Inventory Empty</div>
        <p class="empty-desc">Record a local delivery delivery to start tracking stock batches.</p>
      </div>
    `;
  } else {
    tableMarkup = `
      <div class="table-container">
        <table class="data-table">
          <thead>
            <tr>
              <th>Medicine Item</th>
              <th>Batch ID</th>
              <th>Quantity</th>
              <th>Expiration</th>
              <th>Days of Supply</th>
              <th>Calculated Penalties</th>
            </tr>
          </thead>
          <tbody>
            ${myBatches.map(b => {
              const formattedDate = new Date(b.expiry_date).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
              
              let penaltyBadges = '';
              if (b.expiry_penalty > 0) {
                penaltyBadges += `<span class="badge badge-danger">Expiry Penalty: +${b.expiry_penalty}</span> `;
              }
              if (b.holding_penalty > 0) {
                penaltyBadges += `<span class="badge badge-warning">Holding Penalty: +${b.holding_penalty}</span> `;
              }
              if (b.total_penalty === 0) {
                penaltyBadges = `<span class="badge badge-success" style="text-transform: none;">0 pts (Healthy Stock)</span>`;
              }

              return `
                <tr>
                  <td style="font-weight: 600;">${b.item}</td>
                  <td><code>${b.batch_id}</code></td>
                  <td style="font-weight: 500;">${b.quantity} units</td>
                  <td>
                    <div style="display: flex; flex-direction: column;">
                      <span>${formattedDate}</span>
                      <span style="font-size: 0.75rem; color: ${b.days_to_expiry <= 14 ? 'var(--danger)' : 'var(--text-secondary)'};">
                        ${b.days_to_expiry} days left
                      </span>
                    </div>
                  </td>
                  <td style="font-weight: 600;">${b.days_of_supply} days</td>
                  <td>
                    <div style="display: flex; flex-wrap: wrap; gap: 0.35rem;">
                      ${penaltyBadges}
                    </div>
                  </td>
                </tr>
              `;
            }).join('')}
          </tbody>
        </table>
      </div>
    `;
  }

  target.innerHTML = `
    <div class="panel">
      <div class="panel-header">
        <h2 class="panel-title">Current Stock Batches at ${state.myClinic}</h2>
      </div>
      ${tableMarkup}
    </div>
  `;
}

function renderForecasting(target) {
  const myBatches = state.batches.filter(b => b.clinic === state.myClinic);
  const myItems = [...new Set(myBatches.map(b => b.item))];
  const selectedForecastData = myBatches.find(b => b.item === state.selectedForecastItem);

  const options = myItems.map(item => `
    <option value="${item}" ${state.selectedForecastItem === item ? 'selected' : ''}>${item}</option>
  `).join('');

  let forecastMarkup = '';
  if (!selectedForecastData) {
    forecastMarkup = `
      <div style="text-align: center; color: var(--text-secondary); padding: 3rem;">
        No forecasting data matches the selected medicine.
      </div>
    `;
  } else {
    // Render Day-of-week multipliers chart
    const dowBars = Object.entries(selectedForecastData.weekday_multipliers).map(([day, mult]) => {
      const pct = Math.min(100, (mult / 2.0) * 100);
      return `
        <div class="chart-bar-row">
          <span class="chart-bar-label">${day}</span>
          <div class="chart-bar-track">
            <div class="chart-bar-fill" style="width: ${pct}%; background-color: ${mult >= 1.0 ? 'var(--primary)' : 'var(--secondary)'};"></div>
          </div>
          <span class="chart-bar-val">${mult}x</span>
        </div>
      `;
    }).join('');

    // Render Upcoming 7-day Projections
    const maxVal = Math.max(...selectedForecastData.upcoming_forecast, 1);
    const projBars = selectedForecastData.upcoming_forecast.map((val, idx) => {
      const pct = (val / maxVal) * 100;
      return `
        <div class="chart-bar-row">
          <span class="chart-bar-label">Day +${idx + 1}</span>
          <div class="chart-bar-track">
            <div class="chart-bar-fill" style="width: ${pct}%; background-color: var(--success);"></div>
          </div>
          <span class="chart-bar-val" style="color: var(--success);">${val}</span>
        </div>
      `;
    }).join('');

    forecastMarkup = `
      <!-- Seasonal Insight Banner -->
      <div class="banner" style="background-color: var(--primary-bg); border-color: var(--primary-border); color: var(--primary-dark); display: flex; align-items: center; gap: 0.75rem;">
        ℹ
        <div>
          <strong style="font-size: 0.9rem;">Seasonal Insight:</strong>
          <span style="margin-left: 0.5rem; font-size: 0.88rem;">${selectedForecastData.season_insight}</span>
        </div>
      </div>

      <div class="columns-2">
        <!-- Weekday Multipliers -->
        <div class="panel" style="border: 1px solid var(--border-color); margin: 0; padding: 1.25rem;">
          <h3 style="font-size: 0.95rem; font-weight: 700; margin-bottom: 1rem; color: var(--text-primary);">
            Weekday Multiplier Factors
          </h3>
          <div class="forecast-chart-container">
            ${dowBars}
          </div>
          <p style="font-size: 0.75rem; color: var(--text-muted); margin-top: 1.25rem; line-height: 1.3;">
            Multipliers values are calculated from the past 30 days of stock usage records. A factor of 1.2x represents a 20% increase in baseline demand on that specific weekday.
          </p>
        </div>

        <!-- 7-Day projections -->
        <div class="panel" style="border: 1px solid var(--border-color); margin: 0; padding: 1.25rem;">
          <h3 style="font-size: 0.95rem; font-weight: 700; margin-bottom: 1rem; color: var(--text-primary);">
            Upcoming 7-Day Demand Projection (units)
          </h3>
          <div class="forecast-chart-container">
            ${projBars}
          </div>
          <div style="margin-top: 1.25rem; padding-top: 1rem; border-top: 1px solid var(--border-color); display: flex; justify-content: space-between; font-size: 0.8rem;">
            <span style="color: var(--text-secondary);">Estimated Daily Average:</span>
            <strong style="color: var(--primary);">${selectedForecastData.predicted_daily_usage} units / day</strong>
          </div>
        </div>
      </div>
    `;
  }

  target.innerHTML = `
    <div class="panel">
      <div class="panel-header">
        <h2 class="panel-title">Upcoming Sales & Season Analysis</h2>
        <div style="display: flex; align-items: center; gap: 0.5rem;">
          <span style="font-size: 0.82rem; color: var(--text-secondary); font-weight: 500;">Select Medicine:</span>
          <select class="form-control" style="width: 220px; padding: 0.4rem;" onchange="changeForecastItem(this.value)">
            ${options}
          </select>
        </div>
      </div>
      ${forecastMarkup}
    </div>
  `;
}

function renderNetwork(target) {
  const otherBatches = state.batches.filter(b => b.clinic !== state.myClinic);

  target.innerHTML = `
    <div class="panel">
      <div class="panel-header">
        <h2 class="panel-title">Stock Balances at Sister Clinics</h2>
      </div>
      <div class="table-container">
        <table class="data-table">
          <thead>
            <tr>
              <th>Clinic Name</th>
              <th>Medicine Item</th>
              <th>Batch ID</th>
              <th>Stock Available</th>
              <th>Expiry Date</th>
            </tr>
          </thead>
          <tbody>
            ${otherBatches.map(b => `
              <tr>
                <td style="font-weight: 600;">${b.clinic}</td>
                <td>${b.item}</td>
                <td><code>${b.batch_id}</code></td>
                <td style="font-weight: 500;">${b.quantity} units</td>
                <td>${new Date(b.expiry_date).toLocaleDateString()}</td>
              </tr>
            `).join('')}
          </tbody>
        </table>
      </div>
    </div>
  `;
}

// Initial boot
const CLINIC_LIST = ['Clinic A', 'Clinic B', 'Clinic C'];
fetchData();
