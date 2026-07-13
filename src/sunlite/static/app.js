const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

async function api(url, options = {}) {
  const method = (options.method || "GET").toUpperCase();
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  if (!["GET", "HEAD", "OPTIONS"].includes(method)) {
    headers["X-CSRF-Token"] = $("meta[name='csrf-token']")?.content || "";
    headers["Idempotency-Key"] = crypto.randomUUID
      ? crypto.randomUUID()
      : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  }
  const response = await fetch(url, {
    ...options,
    headers,
  });
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`;
    try { message = (await response.json()).detail || message; } catch (_) { /* response was not JSON */ }
    throw new Error(message);
  }
  return response.json();
}

function toast(message, error = false) {
  const element = $("#toast");
  if (!element) return;
  element.textContent = message;
  element.className = `toast show${error ? " error" : ""}`;
  window.setTimeout(() => { element.className = "toast"; }, 3000);
}

function pretty(value) {
  return String(value || "").replaceAll("_", " ").replace(/\b\w/g, char => char.toUpperCase());
}

function nextLabel(item) {
  if (!item) return "Not scheduled";
  return `${item.state.toUpperCase()} · ${new Date(item.at).toLocaleString([], { dateStyle: "medium", timeStyle: "short" })}`;
}

function updateStatus(status) {
  const fleetState = $("#fleet-state");
  if (fleetState) fleetState.textContent = status.global_stop_latched ? "STOPPED" : "AUTOMATIC";
  const health = $("#controller-health");
  if (health) {
    health.className = `health-line ${status.healthy ? "healthy" : "offline"}`;
    health.innerHTML = `<b aria-hidden="true">${status.healthy ? "✓" : "!"}</b>${status.healthy ? "Controller healthy" : "Controller offline"}`;
  }
  for (const device of status.devices || []) {
    const roots = $$(`[data-device-id="${device.id}"]`);
    for (const root of roots) {
      const state = $("[data-field='state']", root);
      if (state) {
        state.className = state.className.replace(/state-(on|off)/g, "").trim() + ` state-${device.commanded_state}`;
        state.textContent = device.commanded_state.toUpperCase();
      }
      const mode = $("[data-field='mode']", root);
      const active = $("[data-field='active']", root);
      const next = $("[data-field='next']", root);
      const fault = $("[data-field='fault']", root);
      if (mode) mode.textContent = pretty(device.mode);
      if (active) active.textContent = device.active_schedule_name || "None";
      if (next) next.textContent = nextLabel(device.next_transition);
      if (fault) fault.textContent = device.fault ? `Fault: ${device.fault}` : "No faults";
    }
  }
}

function startEvents() {
  if (!$("[data-device-id]")) return;
  const source = new EventSource("/api/events");
  source.onmessage = event => updateStatus(JSON.parse(event.data));
  source.addEventListener("offline", () => toast("Controller connection lost", true));
}

async function runCommand(button) {
  const command = button.dataset.command;
  const payload = {};
  if (button.dataset.device) payload.device_id = button.dataset.device;
  button.disabled = true;
  try {
    const status = await api(`/api/commands/${command}`, { method: "POST", body: JSON.stringify(payload) });
    updateStatus(status);
    toast(command === "stop-all" ? "All outputs commanded OFF" : "Command applied");
  } catch (error) {
    toast(error.message, true);
  } finally {
    button.disabled = false;
  }
}

function setupCommands() {
  document.addEventListener("click", event => {
    const button = event.target.closest("[data-command]");
    if (button) runCommand(button);
  });
  for (const button of $$('[data-delete-schedule]')) {
    button.addEventListener("click", async () => {
      if (!window.confirm("Delete this schedule? Its run history will be retained.")) return;
      try {
        await api(`/api/schedules/${button.dataset.deleteSchedule}`, { method: "DELETE" });
        button.closest(".schedule-row").remove();
        toast("Schedule deleted");
      } catch (error) { toast(error.message, true); }
    });
  }
}

function setupManualDialog() {
  const dialog = $("#manual-dialog");
  if (!dialog) return;
  for (const button of $$('[data-open-manual]')) {
    button.addEventListener("click", () => {
      $("#manual-state").value = button.dataset.openManual;
      $("#manual-state-label").textContent = button.dataset.openManual.toUpperCase();
      dialog.showModal();
    });
  }
  $("#manual-form").addEventListener("submit", async event => {
    if (event.submitter?.value === "cancel") return;
    event.preventDefault();
    const payload = {
      device_id: event.currentTarget.dataset.device,
      state: $("#manual-state").value,
      duration_seconds: Number($("#manual-duration").value) * 60,
      reason: $("#manual-reason").value.trim(),
    };
    try {
      const status = await api("/api/commands/manual", { method: "POST", body: JSON.stringify(payload) });
      updateStatus(status);
      dialog.close();
      toast("Manual override applied");
    } catch (error) { toast(error.message, true); }
  });
}

function localInput(value) {
  const date = value ? new Date(value) : new Date(Date.now() + 5 * 60_000);
  const offset = date.getTimezoneOffset() * 60_000;
  return new Date(date.getTime() - offset).toISOString().slice(0, 16);
}

function addCustomStep(offset = 0, state = "on") {
  const container = $("#custom-steps");
  const row = document.createElement("div");
  row.className = "custom-step";
  row.innerHTML = `<label>Offset <span>minutes</span><input class="step-offset" type="number" min="0" step="0.0167" value="${offset}" required></label>
    <label>State<select class="step-state"><option value="on">ON</option><option value="off">OFF</option></select></label>
    <button type="button" class="icon-button remove-step" aria-label="Remove transition">×</button>`;
  $(".step-state", row).value = state;
  $(".remove-step", row).addEventListener("click", () => row.remove());
  container.append(row);
}

function collectSchedule(existingId = null) {
  const kind = $("#schedule-kind").value;
  const payload = {
    id: existingId || (crypto.randomUUID ? crypto.randomUUID() : `schedule-${Date.now()}`),
    device_id: $("#schedule-device").value,
    name: $("#schedule-name").value.trim(),
    kind,
    starts_at: new Date($("#starts-at").value).toISOString(),
    timezone: $("#schedule-form").dataset.timezone,
    recovery_policy: $("#recovery-policy").value,
    enabled: $("#schedule-enabled").checked,
  };
  if (kind === "regular") {
    payload.on_seconds = Number($("#on-minutes").value) * 60;
    payload.off_seconds = Number($("#off-minutes").value) * 60;
    if ($("#end-rule").value === "repeat") payload.repeat_count = Number($("#repeat-count").value);
    else payload.ends_at = new Date($("#ends-at").value).toISOString();
  } else {
    payload.steps = $$(".custom-step").map(row => ({
      offset_seconds: Number($(".step-offset", row).value) * 60,
      state: $(".step-state", row).value,
    }));
  }
  return payload;
}

function setupScheduleEditor() {
  const form = $("#schedule-form");
  if (!form) return;
  const stored = JSON.parse($("#schedule-data").textContent || "null");
  const requestedDevice = new URLSearchParams(location.search).get("device");
  $("#starts-at").value = localInput(stored?.starts_at);
  if (stored) {
    $("#schedule-name").value = stored.name;
    $("#schedule-device").value = stored.device_id;
    $("#schedule-enabled").checked = stored.enabled;
    $("#recovery-policy").value = stored.recovery_policy;
  } else if (requestedDevice) $("#schedule-device").value = requestedDevice;

  function selectKind(kind) {
    $("#schedule-kind").value = kind;
    $("#regular-fields").hidden = kind !== "regular";
    $("#custom-fields").hidden = kind !== "custom";
    for (const button of $$('[data-kind]')) button.classList.toggle("active", button.dataset.kind === kind);
  }
  for (const button of $$('[data-kind]')) button.addEventListener("click", () => selectKind(button.dataset.kind));
  $("#end-rule").addEventListener("change", event => {
    const time = event.target.value === "time";
    $("#repeat-field").hidden = time;
    $("#end-field").hidden = !time;
    if (time && !$("#ends-at").value) $("#ends-at").value = localInput(Date.now() + 60 * 60_000);
  });
  $("#add-step").addEventListener("click", () => {
    const rows = $$(".custom-step");
    const offset = rows.length ? Number($(".step-offset", rows.at(-1)).value) + 10 : 0;
    const state = rows.length && $(".step-state", rows.at(-1)).value === "on" ? "off" : "on";
    addCustomStep(offset, state);
  });

  if (stored?.kind === "regular") {
    $("#on-minutes").value = stored.on_seconds / 60;
    $("#off-minutes").value = stored.off_seconds / 60;
    if (stored.repeat_count) $("#repeat-count").value = stored.repeat_count;
    else {
      $("#end-rule").value = "time";
      $("#end-rule").dispatchEvent(new Event("change"));
      $("#ends-at").value = localInput(stored.ends_at);
    }
  } else if (stored?.kind === "custom") {
    for (const step of stored.steps) addCustomStep(step.offset_seconds / 60, step.state);
  } else {
    addCustomStep(0, "on");
    addCustomStep(30, "off");
  }
  selectKind(stored?.kind || "regular");

  $("#preview-schedule").addEventListener("click", async () => {
    try {
      const items = await api("/api/schedules/preview", { method: "POST", body: JSON.stringify(collectSchedule(stored?.id)) });
      $("#preview-title").textContent = `${items.length} commanded transitions`;
      $("#preview-list").innerHTML = items.map(item => `<li><strong>${item.state.toUpperCase()}</strong> · ${new Date(item.at).toLocaleString()}</li>`).join("");
    } catch (error) { toast(error.message, true); }
  });
  form.addEventListener("submit", async event => {
    event.preventDefault();
    try {
      await api("/api/schedules", { method: "POST", body: JSON.stringify(collectSchedule(stored?.id)) });
      location.assign("/schedules");
    } catch (error) { toast(error.message, true); }
  });
}

function setupHistoryFilter() {
  const filter = $("#history-device-filter");
  if (!filter) return;
  filter.addEventListener("change", () => {
    for (const row of $$('[data-history-device]')) {
      row.hidden = Boolean(filter.value) && row.dataset.historyDevice !== filter.value;
    }
  });
}

document.addEventListener("DOMContentLoaded", () => {
  setupCommands();
  setupManualDialog();
  setupScheduleEditor();
  setupHistoryFilter();
  startEvents();
});
