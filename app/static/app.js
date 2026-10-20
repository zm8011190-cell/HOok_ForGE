const $ = (selector) => document.querySelector(selector);
const elements = {
  health: $("#sidebar-health"),
  healthDot: $("#sidebar-status-dot"),
  connection: $("#connection-badge"),
  connectionLabel: $("#connection-label"),
  endpointRows: $("#endpoint-rows"),
  deliveryRows: $("#delivery-rows"),
  lastSync: $("#last-sync"),
  toastRegion: $("#toast-region"),
};

let refreshInProgress = false;

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (character) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  })[character]);
}

function hostFromUrl(value) {
  try {
    return new URL(value).host;
  } catch {
    return value;
  }
}

function statusClass(value) {
  return String(value || "").toLowerCase().replace(/[^a-z0-9_-]/g, "");
}

function formatTimestamp(value) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "—" : date.toLocaleString();
}

function makeIdempotencyKey() {
  if (globalThis.crypto?.randomUUID) return `evt_${crypto.randomUUID()}`;
  return `evt_${Date.now()}_${Math.random().toString(36).slice(2, 12)}`;
}

function showToast(message, type = "success") {
  const toast = document.createElement("div");
  toast.className = `toast ${type === "error" ? "error" : ""}`;
  toast.innerHTML = `<div class="toast-title">${type === "error" ? "Request failed" : "Done"}</div><div>${escapeHtml(message)}</div>`;
  elements.toastRegion.append(toast);
  window.setTimeout(() => toast.remove(), 5200);
}

async function request(path, options = {}) {
  let response;
  try {
    response = await fetch(path, {
      ...options,
      headers: {
        ...(options.body ? { "Content-Type": "application/json" } : {}),
        ...options.headers,
      },
    });
  } catch {
    throw new Error("Could not connect to the API. Check that Hook Forge is running.");
  }

  if (!response.ok) {
    let message = `Request failed with status ${response.status}.`;
    try {
      const body = await response.json();
      if (typeof body.detail === "string") message = body.detail;
      else if (Array.isArray(body.detail)) {
        message = body.detail.map((error) => error.msg).join(", ") || message;
      }
    } catch {
      // Keep the HTTP status message when the response isn't JSON.
    }
    throw new Error(message);
  }
  return response.status === 204 ? null : response.json();
}

function setHealth(isOnline) {
  elements.health.textContent = isOnline ? "API responding" : "API unreachable";
  elements.healthDot.classList.toggle("online", isOnline);
  elements.healthDot.classList.toggle("offline", !isOnline);
  elements.connection.classList.toggle("online", isOnline);
  elements.connection.classList.toggle("offline", !isOnline);
  elements.connectionLabel.textContent = isOnline ? "API CONNECTED" : "API UNREACHABLE";
}

function renderEndpoints(endpoints) {
  $("#metric-endpoints").textContent = endpoints.length;
  if (!endpoints.length) {
    elements.endpointRows.innerHTML = '<tr><td colspan="3" class="empty-cell">No endpoints yet. Register one to start receiving events.</td></tr>';
    return;
  }
  elements.endpointRows.innerHTML = endpoints.map((endpoint) => `
    <tr>
      <td><div class="endpoint-cell"><span class="endpoint-icon">↗</span><div style="min-width:0"><div class="endpoint-url">${escapeHtml(endpoint.url)}</div><div class="endpoint-host">${escapeHtml(hostFromUrl(endpoint.url))}</div></div></div></td>
      <td><span class="status-pill">ACTIVE</span></td>
      <td class="row-id">#${escapeHtml(endpoint.id)}</td>
    </tr>`).join("");
}

function renderDeliveries(deliveries) {
  const delivered = deliveries.filter((item) => item.status === "DELIVERED").length;
  const failed = deliveries.filter((item) => item.status === "FAILED").length;
  const completed = delivered + failed;
  const successRate = completed ? Math.round((delivered / completed) * 100) : 0;

  $("#metric-deliveries").textContent = deliveries.length;
  $("#delivery-window").textContent = "50";
  $("#metric-success").innerHTML = `${successRate}<small>%</small>`;
  $("#metric-delivered").textContent = `${delivered} delivered`;
  $("#metric-failed").textContent = failed;
  $("#failed-caption").textContent = failed === 1 ? "Failed delivery" : "Failed deliveries";
  $("#failed-bullet").classList.toggle("alert", failed > 0);
  $("#delivery-count").textContent = `${deliveries.length} RECORD${deliveries.length === 1 ? "" : "S"}`;

  if (!deliveries.length) {
    elements.deliveryRows.innerHTML = '<tr><td colspan="7" class="empty-cell">No deliveries yet. Publish an event to see activity here.</td></tr>';
    return;
  }

  elements.deliveryRows.innerHTML = deliveries.map((delivery) => {
    const state = statusClass(delivery.status);
    const canRetry = delivery.status === "FAILED" || delivery.status === "RETRYING";
    const response = delivery.response_status_code
      ? `<span class="response-code">${escapeHtml(delivery.response_status_code)}</span>`
      : '<span class="response-code">—</span>';
    const latency = delivery.latency_ms == null
      ? ""
      : `<span class="latency-cell">${escapeHtml(delivery.latency_ms)} ms</span>`;
    return `
      <tr>
        <td class="delivery-id">#${escapeHtml(delivery.id)}</td>
        <td class="event-name-cell">event #${escapeHtml(delivery.event_id)}</td>
        <td class="endpoint-id-cell">endpoint #${escapeHtml(delivery.endpoint_id)}</td>
        <td><span class="status-pill status-${state}">${escapeHtml(delivery.status)}</span></td>
        <td class="attempt-count">${escapeHtml(delivery.delivery_attempts)}</td>
        <td>${response}${latency}</td>
        <td>${canRetry ? `<button class="retry-button" type="button" data-retry-id="${escapeHtml(delivery.id)}">↻ Retry</button>` : ""}</td>
      </tr>`;
  }).join("");
}

async function refreshDashboard() {
  if (refreshInProgress) return;
  refreshInProgress = true;
  const refreshButton = $("#refresh-button");
  refreshButton.classList.add("spinning");
  try {
    const health = await request("/health");
    setHealth(health?.status === "ok");
  } catch {
    setHealth(false);
  }

  const [endpointsResult, deliveriesResult] = await Promise.allSettled([
    request("/endpoints"),
    request("/deliveries"),
  ]);

  if (endpointsResult.status === "fulfilled") {
    renderEndpoints(endpointsResult.value);
  } else {
    elements.endpointRows.innerHTML = '<tr><td colspan="3" class="empty-cell">Could not load endpoints. Try refreshing.</td></tr>';
    showToast(endpointsResult.reason.message, "error");
  }
  if (deliveriesResult.status === "fulfilled") {
    renderDeliveries(deliveriesResult.value);
  } else {
    elements.deliveryRows.innerHTML = '<tr><td colspan="7" class="empty-cell">Could not load deliveries. Try refreshing.</td></tr>';
    showToast(deliveriesResult.reason.message, "error");
  }
  elements.lastSync.textContent = new Date().toLocaleTimeString();
  refreshButton.classList.remove("spinning");
  refreshInProgress = false;
}

function scrollToForm(id) {
  const input = document.getElementById(id);
  input?.scrollIntoView({ behavior: "smooth", block: "center" });
  input?.focus({ preventScroll: true });
}

document.querySelectorAll("[data-open-form]").forEach((button) => {
  button.addEventListener("click", () => scrollToForm(button.dataset.openForm));
});

document.querySelectorAll(".nav-item").forEach((link) => {
  link.addEventListener("click", () => {
    document.querySelectorAll(".nav-item").forEach((item) => item.classList.remove("active"));
    link.classList.add("active");
  });
});

$("#refresh-button").addEventListener("click", refreshDashboard);
$("#deliveries-refresh").addEventListener("click", refreshDashboard);

$("#regenerate-key").addEventListener("click", () => {
  $("#event-key").value = makeIdempotencyKey();
});

$("#event-key").value = makeIdempotencyKey();

$("#event-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const button = form.querySelector('[type="submit"]');
  const result = $("#event-result");
  result.className = "form-result";
  result.textContent = "";

  let payload;
  try {
    payload = JSON.parse($("#event-payload").value);
  } catch {
    result.classList.add("error");
    result.textContent = "Payload must be valid JSON.";
    $("#event-payload").focus();
    return;
  }
  if (!payload || Array.isArray(payload) || typeof payload !== "object") {
    result.classList.add("error");
    result.textContent = "Payload must be a JSON object.";
    $("#event-payload").focus();
    return;
  }

  button.disabled = true;
  button.querySelector("span").textContent = "Publishing…";
  try {
    const created = await request("/events", {
      method: "POST",
      body: JSON.stringify({
        name: $("#event-name").value.trim(),
        payload_json: payload,
        idempotency_key: $("#event-key").value.trim(),
      }),
    });
    result.textContent = `Event #${created.id} accepted · ${created.name}`;
    form.reset();
    $("#event-payload").value = '{\n  "order_id": 123\n}';
    $("#event-key").value = makeIdempotencyKey();
    showToast(`Event "${created.name}" accepted.`);
    await refreshDashboard();
  } catch (error) {
    result.classList.add("error");
    result.textContent = error.message;
    showToast(error.message, "error");
  } finally {
    button.disabled = false;
    button.querySelector("span").textContent = "Publish event";
  }
});

$("#endpoint-form")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const button = form.querySelector('[type="submit"]');
  const result = $("#endpoint-result");
  button.disabled = true;
  button.textContent = "Registering…";
  result.className = "form-result";
  result.textContent = "";
  try {
    const endpoint = await request("/endpoints", {
      method: "POST",
      body: JSON.stringify({ url: $("#endpoint-url").value.trim() }),
    });
    result.innerHTML = `Endpoint #${escapeHtml(endpoint.id)} registered. Save this signing secret now; it is only shown once:<div class="secret-output">${escapeHtml(endpoint.secret)}</div>`;
    result.classList.remove("error");
    form.reset();
    showToast("Endpoint registered. Copy its signing secret before leaving this page.");
    await refreshDashboard();
  } catch (error) {
    result.classList.add("error");
    result.textContent = error.message;
    showToast(error.message, "error");
  } finally {
    button.disabled = false;
    button.textContent = "Register endpoint";
  }
});

elements.deliveryRows.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-retry-id]");
  if (!button) return;
  const deliveryId = button.dataset.retryId;
  button.disabled = true;
  button.textContent = "Queueing…";
  try {
    await request(`/deliveries/${encodeURIComponent(deliveryId)}/reply`, { method: "POST" });
    showToast(`Delivery #${deliveryId} queued for retry.`);
    await refreshDashboard();
  } catch (error) {
    button.disabled = false;
    button.textContent = "↻ Retry";
    showToast(error.message, "error");
  }
});

refreshDashboard();
window.setInterval(() => {
  if (!document.hidden) refreshDashboard();
}, 15000);
