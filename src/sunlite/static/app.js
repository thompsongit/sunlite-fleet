const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

let liveStatus = null;
let serverClockOffset = 0;

async function api(url, options = {}) {
  const method = (options.method || "GET").toUpperCase();
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  if (!["GET", "HEAD", "OPTIONS"].includes(method)) {
    headers["X-CSRF-Token"] = $("meta[name='csrf-token']")?.content || "";
    headers["Idempotency-Key"] = crypto.randomUUID
      ? crypto.randomUUID()
      : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  }
  const response = await fetch(url, { ...options, headers });
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`;
    try { message = (await response.json()).detail || message; } catch (_) { /* not JSON */ }
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
  if (value === "ocp") return "OCP";
  return String(value || "").replaceAll("_", " ").replace(/\b\w/g, char => char.toUpperCase());
}

function numberText(value) {
  const numeric = Number(value);
  return Number.isInteger(numeric) ? String(numeric) : String(Number(numeric.toFixed(6)));
}

function durationLabel(value) {
  const seconds = Number(value);
  if (!Number.isFinite(seconds)) return "—";
  const plain = `${numberText(seconds)} s`;
  if (seconds < 60) return plain;
  const minutes = Math.floor(seconds / 60);
  const remainder = seconds - minutes * 60;
  return `${plain} (${minutes} min ${numberText(remainder)} s)`;
}

function countdownLabel(target) {
  if (!target) return "—";
  const remaining = Math.max(0, (Date.parse(target) - (Date.now() + serverClockOffset)) / 1000);
  const rounded = Math.ceil(remaining);
  const hours = Math.floor(rounded / 3600);
  const minutes = Math.floor((rounded % 3600) / 60);
  const seconds = rounded % 60;
  return `${hours ? `${String(hours).padStart(2, "0")}:` : ""}${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")} remaining`;
}

function formatDateTime(value, timezone) {
  if (!value) return "—";
  const formatted = new Intl.DateTimeFormat("en-ZA", {
    timeZone: timezone,
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(new Date(value));
  const zoneLabel = timezone === "Africa/Johannesburg" ? "SAST" : timezone;
  return `${formatted} ${zoneLabel}`;
}

function localInputInZone(value, timezone) {
  const instant = value ? new Date(value) : new Date(Date.now() + 5 * 60_000);
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: timezone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hourCycle: "h23",
  }).formatToParts(instant);
  const values = Object.fromEntries(parts.map(part => [part.type, part.value]));
  return `${values.year}-${values.month}-${values.day}T${values.hour}:${values.minute}:${values.second}`;
}

function nextLabel(item, timezone) {
  if (!item) return "Not scheduled";
  return `${item.state.toUpperCase()} · ${formatDateTime(item.at, timezone)}`;
}

function preparationLabels(device) {
  if (device.phase === "handoff") {
    return {
      handoff: countdownLabel(device.phase_ends_at),
      ocp: "Starts after handoff delay",
    };
  }
  if (device.phase === "ocp") {
    return {
      handoff: "Complete ✓",
      ocp: countdownLabel(device.phase_ends_at),
    };
  }
  if (device.phase === "running") {
    return { handoff: "Complete ✓", ocp: "Complete ✓" };
  }
  return { handoff: "Not active", ocp: "Not active" };
}

function updateStatus(status) {
  liveStatus = status;
  if (status.now) serverClockOffset = Date.parse(status.now) - Date.now();
  const timezone = status.timezone || "Africa/Johannesburg";
  const fleetState = $("#fleet-state");
  if (fleetState) fleetState.textContent = status.global_stop_latched ? "STOPPED" : "AUTOMATIC";
  const health = $("#controller-health");
  if (health) {
    health.className = `health-line ${status.healthy ? "healthy" : "offline"}`;
    health.innerHTML = `<b aria-hidden="true">${status.healthy ? "✓" : "!"}</b>${status.healthy ? "Controller healthy" : "Controller offline"}`;
  }
  for (const device of status.devices || []) {
    const labels = preparationLabels(device);
    for (const root of $$(`[data-device-id="${device.id}"]`)) {
      const state = $("[data-field='state']", root);
      if (state) {
        state.className = state.className.replace(/state-(on|off)/g, "").trim() + ` state-${device.commanded_state}`;
        state.textContent = device.commanded_state.toUpperCase();
      }
      const values = {
        mode: pretty(device.mode),
        active: device.active_schedule_name || "None",
        next: nextLabel(device.next_transition, timezone),
        fault: device.fault ? `Fault: ${device.fault}` : "No faults",
        phase: pretty(device.phase),
        handoff: labels.handoff,
        ocp: labels.ocp,
        handoffConfigured: `${durationLabel(device.handoff_seconds || 0)} configured`,
        ocpConfigured: `${durationLabel(device.ocp_seconds || 0)} configured`,
        elapsed: durationLabel(device.run_elapsed_seconds || 0),
        firstLight: formatDateTime(device.first_light_at, timezone),
      };
      for (const [field, value] of Object.entries(values)) {
        const element = $(`[data-field='${field}']`, root);
        if (element) element.textContent = value;
      }
      for (const button of $$("[data-command='pause'], [data-open-manual='on']", root)) {
        button.disabled = ["handoff", "ocp"].includes(device.phase);
      }
    }
  }
}

function startEvents() {
  if (!$("[data-device-id]")) return;
  const source = new EventSource("/api/events");
  source.onmessage = event => updateStatus(JSON.parse(event.data));
  source.addEventListener("offline", () => toast("Controller connection lost", true));
  window.setInterval(() => {
    if (liveStatus) updateStatus(liveStatus);
  }, 500);
}

async function runCommand(button) {
  const command = button.dataset.command;
  const payload = {};
  if (button.dataset.device) payload.device_id = button.dataset.device;
  button.disabled = true;
  try {
    const status = await api(`/api/commands/${command}`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
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
  for (const button of $$("[data-delete-schedule]")) {
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
  for (const button of $$("[data-open-manual]")) {
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
      const status = await api("/api/commands/manual", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      updateStatus(status);
      dialog.close();
      toast("Manual override applied");
    } catch (error) { toast(error.message, true); }
  });
}

function addCustomStep(kind, runTime = 0, state = "on", first = false) {
  const container = $(`#${kind === "on_demand" ? "on-demand" : "custom"}-steps`);
  const row = document.createElement("div");
  row.className = "custom-step";
  row.innerHTML = `<label>Run time <span>seconds</span><input class="step-time" type="number" min="0" step="any" value="${runTime}" required${first ? " readonly" : ""}></label>
    <label>State<select class="step-state"${first ? " data-locked=\"true\"" : ""}><option value="on">ON</option><option value="off">OFF</option></select></label>
    <button type="button" class="icon-button remove-step" aria-label="Remove transition"${first ? " hidden" : ""}>×</button>`;
  $(".step-state", row).value = state;
  if (first) $(".step-state", row).disabled = true;
  $(".remove-step", row).addEventListener("click", () => row.remove());
  container.append(row);
}

function selectedPreparation(kind) {
  const prefix = kind === "on_demand" ? "on-demand" : kind;
  return {
    handoff: Number($(`#${prefix}-handoff`).value),
    ocp: Number($(`#${prefix}-ocp`).value),
  };
}

function scheduleSteps(kind) {
  const container = $(`#${kind === "on_demand" ? "on-demand" : "custom"}-steps`);
  return $$(".custom-step", container).map(row => ({
    offset_seconds: Number($(".step-time", row).value),
    state: $(".step-state", row).value,
  }));
}

function collectSchedule(existingId = null) {
  const kind = $("#schedule-kind").value;
  const preparation = selectedPreparation(kind);
  const payload = {
    id: existingId || (crypto.randomUUID ? crypto.randomUUID() : `schedule-${Date.now()}`),
    device_id: $("#schedule-device").value,
    name: $("#schedule-name").value.trim(),
    kind,
    timezone: $("#schedule-form").dataset.timezone,
    recovery_policy: $("#recovery-policy").value,
    enabled: $("#schedule-enabled").checked,
    handoff_seconds: preparation.handoff,
    ocp_seconds: preparation.ocp,
  };
  if (kind === "on_demand") {
    payload.steps = scheduleSteps(kind);
  } else if (kind === "regular") {
    payload.starts_at_local = $("#regular-starts-at").value;
    payload.on_seconds = Number($("#on-seconds").value);
    payload.off_seconds = Number($("#off-seconds").value);
    if ($("#end-rule").value === "repeat") payload.repeat_count = Number($("#repeat-count").value);
    else payload.ends_at_local = $("#ends-at").value;
  } else {
    payload.starts_at_local = $("#custom-starts-at").value;
    payload.steps = scheduleSteps(kind);
  }
  return payload;
}

function setupScheduleEditor() {
  const form = $("#schedule-form");
  if (!form) return;
  const stored = JSON.parse($("#schedule-data").textContent || "null");
  const timezone = form.dataset.timezone;
  const requestedDevice = new URLSearchParams(location.search).get("device");
  const errorPanel = $("#schedule-error");

  function clearError() {
    errorPanel.hidden = true;
    errorPanel.textContent = "";
    for (const field of $$("[aria-invalid='true']", form)) field.removeAttribute("aria-invalid");
  }

  function showError(message, field = null) {
    errorPanel.textContent = message;
    errorPanel.hidden = false;
    if (field) {
      field.setAttribute("aria-invalid", "true");
      field.focus();
    }
    toast(message, true);
    return false;
  }

  function setControlsEnabled(root, enabled) {
    for (const control of $$("input, select, textarea, button", root)) {
      if (!control.matches("[data-kind]")) {
        control.disabled = !enabled || control.dataset.locked === "true";
      }
    }
  }

  function syncEndRule() {
    const active = $("#schedule-kind").value === "regular";
    const byTime = $("#end-rule").value === "time";
    $("#repeat-field").hidden = byTime;
    $("#end-field").hidden = !byTime;
    $("#repeat-count").disabled = !active || byTime;
    $("#repeat-count").required = active && !byTime;
    $("#ends-at").disabled = !active || !byTime;
    $("#ends-at").required = active && byTime;
    if (active && byTime && !$("#ends-at").value) {
      $("#ends-at").value = localInputInZone(Date.now() + 60 * 60_000, timezone);
    }
  }

  function syncFirstOn(kind) {
    if (kind === "regular") return;
    const preparation = selectedPreparation(kind);
    const container = $(`#${kind === "on_demand" ? "on-demand" : "custom"}-steps`);
    const first = $(".custom-step", container);
    if (first) $(".step-time", first).value = numberText(preparation.ocp);
    const panel = $(`[data-mode-panel="${kind}"]`);
    const marker = $(".ocp-marker", panel);
    if (marker) marker.hidden = preparation.ocp === 0;
  }

  function validateEditor() {
    clearError();
    if (!form.reportValidity()) {
      return showError("Complete the highlighted required field.", $(":invalid", form));
    }
    const kind = $("#schedule-kind").value;
    const prep = selectedPreparation(kind);
    if (![prep.handoff, prep.ocp].every(value => Number.isFinite(value) && value >= 0)) {
      return showError("Handoff delay and OCP period must be finite non-negative seconds.");
    }
    if (kind === "regular") {
      if ($("#end-rule").value === "time" && $("#ends-at").value <= $("#regular-starts-at").value) {
        return showError("End date and time must be after the scheduled time.", $("#ends-at"));
      }
      return true;
    }
    const container = $(`#${kind === "on_demand" ? "on-demand" : "custom"}-steps`);
    const rows = $$(".custom-step", container);
    if (rows.length < 2) return showError("Add at least two custom transitions.");
    const steps = rows.map(row => ({
      time: Number($(".step-time", row).value),
      state: $(".step-state", row).value,
      field: $(".step-time", row),
    }));
    if (steps[0].time !== prep.ocp || steps[0].state !== "on") {
      return showError("First light ON time must equal the OCP period.", steps[0].field);
    }
    if (steps.at(-1).state !== "off") {
      return showError("The custom timeline must end OFF.", $(".step-state", rows.at(-1)));
    }
    for (let index = 1; index < steps.length; index += 1) {
      if (!Number.isFinite(steps[index].time) || steps[index].time <= steps[index - 1].time) {
        return showError("Run times must be finite and strictly increasing.", steps[index].field);
      }
      if (steps[index].state === steps[index - 1].state) {
        return showError("Custom transition states must alternate.", $(".step-state", rows[index]));
      }
    }
    return true;
  }

  function selectKind(kind) {
    $("#schedule-kind").value = kind;
    for (const panel of $$("[data-mode-panel]")) {
      const active = panel.dataset.modePanel === kind;
      panel.hidden = !active;
      setControlsEnabled(panel, active);
    }
    for (const button of $$("[data-kind]")) {
      button.classList.toggle("active", button.dataset.kind === kind);
    }
    syncEndRule();
    syncFirstOn(kind);
    clearError();
  }

  for (const button of $$("[data-kind]")) {
    button.addEventListener("click", () => selectKind(button.dataset.kind));
  }
  $("#end-rule").addEventListener("change", syncEndRule);
  for (const button of $$("[data-add-step]")) {
    button.addEventListener("click", () => {
      const kind = button.dataset.addStep;
      const container = $(`#${kind === "on_demand" ? "on-demand" : "custom"}-steps`);
      const rows = $$(".custom-step", container);
      const time = rows.length ? Number($(".step-time", rows.at(-1)).value) + 10 : 0;
      const state = rows.length && $(".step-state", rows.at(-1)).value === "on" ? "off" : "on";
      addCustomStep(kind, time, state);
    });
  }
  for (const kind of ["on_demand", "custom"]) {
    const prefix = kind === "on_demand" ? "on-demand" : kind;
    $(`#${prefix}-ocp`).addEventListener("input", () => syncFirstOn(kind));
  }

  $("#schedule-name").value = stored?.name || "";
  if (stored) {
    $("#schedule-device").value = stored.device_id;
    $("#schedule-enabled").checked = stored.enabled;
    $("#recovery-policy").value = stored.recovery_policy;
  } else if (requestedDevice) {
    $("#schedule-device").value = requestedDevice;
  }

  $("#regular-starts-at").value = localInputInZone(
    stored?.kind === "regular" ? stored.starts_at : null,
    timezone,
  );
  $("#custom-starts-at").value = localInputInZone(
    stored?.kind === "custom" ? stored.starts_at : null,
    timezone,
  );

  addCustomStep("on_demand", 0, "on", true);
  addCustomStep("on_demand", 30, "off");
  addCustomStep("custom", 0, "on", true);
  addCustomStep("custom", 30, "off");

  if (stored?.kind === "regular") {
    $("#regular-handoff").value = stored.handoff_seconds;
    $("#regular-ocp").value = stored.ocp_seconds;
    $("#on-seconds").value = stored.on_seconds;
    $("#off-seconds").value = stored.off_seconds;
    if (stored.repeat_count) $("#repeat-count").value = stored.repeat_count;
    else {
      $("#end-rule").value = "time";
      $("#ends-at").value = localInputInZone(stored.ends_at, timezone);
    }
  } else if (stored?.kind === "custom" || stored?.kind === "on_demand") {
    const kind = stored.kind;
    const prefix = kind === "on_demand" ? "on-demand" : kind;
    $(`#${prefix}-handoff`).value = stored.handoff_seconds;
    $(`#${prefix}-ocp`).value = stored.ocp_seconds;
    const container = $(`#${prefix}-steps`);
    container.innerHTML = "";
    stored.steps.forEach((step, index) => {
      addCustomStep(kind, step.offset_seconds, step.state, index === 0);
    });
  }

  selectKind(stored?.kind || "on_demand");
  if (stored) {
    for (const button of $$("[data-kind]")) {
      button.disabled = button.dataset.kind !== stored.kind;
    }
  }
  form.addEventListener("input", clearError);

  $("#preview-schedule").addEventListener("click", async () => {
    if (!validateEditor()) return;
    try {
      const payload = collectSchedule(stored?.id);
      const items = await api("/api/schedules/preview", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      $("#preview-title").textContent = `${items.length} commanded transitions`;
      $("#preview-summary").textContent =
        `Handoff delay: ${durationLabel(payload.handoff_seconds)} · OCP period: ${durationLabel(payload.ocp_seconds)}`;
      if (payload.kind === "on_demand") {
        $("#preview-list").innerHTML = items.map(item =>
          `<li><strong>${item.state.toUpperCase()}</strong> · Run time ${durationLabel(item.run_seconds)}${item.phase === "ocp" ? " · OCP begins" : ""}</li>`
        ).join("");
      } else {
        $("#preview-list").innerHTML = items.map(item => {
          const relative = item.run_seconds === null
            ? "Handoff delay"
            : `Run time ${durationLabel(item.run_seconds)}`;
          return `<li><strong>${item.state.toUpperCase()}</strong> · ${relative} · ${formatDateTime(item.at, timezone)}</li>`;
        }).join("");
      }
    } catch (error) { showError(error.message); }
  });

  form.addEventListener("submit", async event => {
    event.preventDefault();
    if (!validateEditor()) return;
    try {
      await api("/api/schedules", {
        method: "POST",
        body: JSON.stringify(collectSchedule(stored?.id)),
      });
      location.assign("/schedules");
    } catch (error) { showError(error.message); }
  });
}

function setupLaunchDialog() {
  const dialog = $("#launch-dialog");
  if (!dialog) return;
  for (const button of $$("[data-launch-schedule]")) {
    button.addEventListener("click", () => {
      dialog.dataset.schedule = button.dataset.launchSchedule;
      dialog.dataset.device = button.dataset.device;
      $("#launch-name").textContent = button.dataset.name;
      $("#launch-device").textContent = button.dataset.deviceName;
      $("#launch-handoff").value = button.dataset.handoff;
      $("#launch-ocp").value = button.dataset.ocp;
      $("#launch-handoff-summary").textContent = durationLabel(button.dataset.handoff);
      $("#launch-ocp-summary").textContent = durationLabel(button.dataset.ocp);
      dialog.showModal();
    });
  }
  for (const input of [$("#launch-handoff"), $("#launch-ocp")]) {
    input.addEventListener("input", () => {
      $("#launch-handoff-summary").textContent = durationLabel($("#launch-handoff").value);
      $("#launch-ocp-summary").textContent = durationLabel($("#launch-ocp").value);
    });
  }
  $("#launch-form").addEventListener("submit", async event => {
    if (event.submitter?.value === "cancel") return;
    event.preventDefault();
    const handoff = Number($("#launch-handoff").value);
    const ocp = Number($("#launch-ocp").value);
    if (![handoff, ocp].every(value => Number.isFinite(value) && value >= 0)) {
      toast("Handoff delay and OCP period must be non-negative seconds", true);
      return;
    }
    try {
      await api(`/api/schedules/${dialog.dataset.schedule}/launch`, {
        method: "POST",
        body: JSON.stringify({ handoff_seconds: handoff, ocp_seconds: ocp }),
      });
      location.assign(`/devices/${dialog.dataset.device}`);
    } catch (error) { toast(error.message, true); }
  });
}

function setupHistoryFilter() {
  const filter = $("#history-device-filter");
  if (!filter) return;
  filter.addEventListener("change", () => {
    for (const row of $$("[data-history-device]")) {
      row.hidden = Boolean(filter.value) && row.dataset.historyDevice !== filter.value;
    }
  });
}

document.addEventListener("DOMContentLoaded", () => {
  setupCommands();
  setupManualDialog();
  setupScheduleEditor();
  setupLaunchDialog();
  setupHistoryFilter();
  startEvents();
});
