/**
 * SenoNetwork Dashboard Client Application
 * Modular ES6+ JavaScript client with EventSource (SSE) real-time pipeline tracking.
 */

// =============================================================================
// 1. API Client with API-Key Header Injection
// =============================================================================
class APIClient {
  static getApiKey() {
    return localStorage.getItem("senonetwork_api_key") || "";
  }

  static setApiKey(key) {
    if (key) {
      localStorage.setItem("senonetwork_api_key", key.trim());
    } else {
      localStorage.removeItem("senonetwork_api_key");
    }
  }

  static async request(endpoint, options = {}) {
    const headers = options.headers || {};
    const apiKey = this.getApiKey();
    if (apiKey) {
      headers["X-API-Key"] = apiKey;
    }
    if (options.body && typeof options.body === "object" && !(options.body instanceof FormData)) {
      headers["Content-Type"] = "application/json";
      options.body = JSON.stringify(options.body);
    }
    options.headers = headers;

    const res = await fetch(endpoint, options);
    if (!res.ok) {
      let errMsg = `HTTP Error ${res.status} (${res.statusText})`;
      try {
        const errJson = await res.json();
        if (errJson.detail) errMsg = typeof errJson.detail === "string" ? errJson.detail : JSON.stringify(errJson.detail);
      } catch (e) {
        // fallback
      }
      throw new Error(errMsg);
    }

    const contentType = res.headers.get("content-type") || "";
    if (contentType.includes("application/json")) {
      return await res.json();
    }
    return await res.text();
  }

  static getHealth() { return this.request("/health"); }
  static getDashboardOverview() { return this.request("/api/dashboard/overview"); }
  static getCupFiles() { return this.request("/api/cup/files"); }
  static downloadLatestCup() { return this.request("/api/cup/download-latest", { method: "POST" }); }
  static getCupPatients(params = {}) {
    const q = new URLSearchParams(params).toString();
    return this.request(`/api/cup/patients?${q}`);
  }
  static processPatientAsync(payload) {
    return this.request("/api/pipeline/process-patient-async", { method: "POST", body: payload });
  }
  static processBatchAsync(payload) {
    return this.request("/api/pipeline/process-batch-async", { method: "POST", body: payload });
  }
  static getTaskStatus(taskId) {
    return this.request(`/api/tasks/${taskId}`);
  }
  static getAuditReport() {
    return this.request("/api/audit/report");
  }
  static getIncompletePatients() {
    return this.request("/api/sync/incomplete-patients");
  }
  static triggerSync(payload = {}) {
    return this.request("/api/sync/check-updates-async", { method: "POST", body: payload });
  }
  static getRedCapRecords(limit = 100) {
    return this.request(`/api/redcap/records?limit=${limit}`);
  }
  static deleteRedCapRecord(recordId) {
    return this.request(`/api/redcap/records/${recordId}`, { method: "DELETE" });
  }
  static verifyPriamoPatient(payload) {
    return this.request("/api/priamo/verify-patient", { method: "POST", body: payload });
  }
}

// =============================================================================
// 2. Tab Navigation Controller (conforme a modern-web-guidance tabs)
// =============================================================================
class TabManager {
  static init() {
    const tabButtons = document.querySelectorAll('[role="tab"]');
    tabButtons.forEach(btn => {
      btn.addEventListener("click", () => {
        const targetPanelId = btn.getAttribute("aria-controls");
        this.switchToTab(btn.id, targetPanelId);
      });
    });
  }

  static switchToTab(buttonId, panelId) {
    const tabButtons = document.querySelectorAll('[role="tab"]');
    const tabPanels = document.querySelectorAll('[role="tabpanel"]');

    tabButtons.forEach(btn => {
      const isSelected = btn.id === buttonId;
      btn.setAttribute("aria-selected", isSelected ? "true" : "false");
    });

    tabPanels.forEach(panel => {
      if (panel.id === panelId) {
        panel.removeAttribute("hidden");
        panel.focus();
      } else {
        panel.setAttribute("hidden", "until-found");
      }
    });

    // Auto-fetch data for the activated panel
    if (panelId === "panel-overview") OverviewController.load();
    if (panelId === "panel-cup") CupController.loadFiles();
    if (panelId === "panel-audit") AuditController.load();
    if (panelId === "panel-sync") SyncController.load();
    if (panelId === "panel-redcap") RedCapController.load();
  }
}

// =============================================================================
// 3. Panoramica & KPI Controller
// =============================================================================
class OverviewController {
  static async load() {
    try {
      const data = await APIClient.getDashboardOverview();
      this.renderHealth(data.health);
      this.renderKPIs(data);
    } catch (err) {
      console.warn("Caricamento overview non riuscito:", err);
      // Fallback health check
      try {
        const health = await APIClient.getHealth();
        this.renderHealth(health);
      } catch (hErr) {
        document.getElementById("global-status-text").textContent = "Server Offline";
        document.getElementById("global-status-dot").className = "status-dot error";
      }
    }
  }

  static renderHealth(health) {
    const dot = document.getElementById("global-status-dot");
    const pillText = document.getElementById("global-status-text");

    if (health.status === "online") {
      dot.className = "status-dot pulse";
      pillText.textContent = `Online (IA: ${health.ai_backend_configured || 'llama_cpp'})`;
    } else {
      dot.className = "status-dot warning";
      pillText.textContent = "Parziale";
    }

    const llamaEl = document.getElementById("status-llama-rpc");
    if (llamaEl) {
      llamaEl.innerHTML = health.llama_cpp_rpc_available
        ? '<span class="badge badge-success">✓ Disponibile (RPC 2 GPU)</span>'
        : '<span class="badge badge-danger">✗ Non risponde</span>';
    }

    const ollamaEl = document.getElementById("status-ollama");
    if (ollamaEl) {
      ollamaEl.innerHTML = health.ollama_available
        ? '<span class="badge badge-success">✓ Attivo (Fallback)</span>'
        : '<span class="badge badge-neutral">Non attivo</span>';
    }

    const redcapEl = document.getElementById("status-redcap");
    if (redcapEl) {
      redcapEl.innerHTML = health.redcap_configured
        ? '<span class="badge badge-success">✓ Configurato (Valduce)</span>'
        : '<span class="badge badge-warning">Token assente</span>';
    }

    const priamoEl = document.getElementById("status-priamo");
    if (priamoEl) {
      priamoEl.innerHTML = health.priamo_dotnet_configured
        ? '<span class="badge badge-success">✓ Connesso (HIS / CUP)</span>'
        : '<span class="badge badge-warning">Priamo non attivo</span>';
    }
  }

  static renderKPIs(data) {
    if (data.cup_metrics) {
      document.getElementById("kpi-cup-files").textContent = data.cup_metrics.total_files || 0;
      if (data.cup_metrics.latest_file) {
        document.getElementById("cup-latest-desc").textContent = data.cup_metrics.latest_file.name;
        document.getElementById("cup-latest-details").textContent =
          `Dimensione: ${(data.cup_metrics.latest_file.size_bytes / 1024).toFixed(1)} KB`;
      }
    }

    if (data.redcap_metrics) {
      document.getElementById("kpi-redcap-records").textContent = data.redcap_metrics.records_count || 0;
      document.getElementById("kpi-completed-records").textContent = data.redcap_metrics.fully_completed_records || 0;
    }

    if (data.storage_metrics) {
      document.getElementById("kpi-downloaded-pdfs").textContent = data.storage_metrics.downloaded_pdfs || 0;
    }
  }
}

// =============================================================================
// 4. Priamo CUP Controller (conforme a modern-web-guidance responsive-table)
// =============================================================================
class CupController {
  static currentPage = 1;
  static pageSize = 20;
  static currentFile = "";

  static async loadFiles() {
    try {
      const res = await APIClient.getCupFiles();
      const select = document.getElementById("cup-file-select");
      select.innerHTML = "";

      if (res.files && res.files.length > 0) {
        res.files.forEach(f => {
          const opt = document.createElement("option");
          opt.value = f.name;
          opt.textContent = `${f.name} (${f.records_estimate || '?'} pazienti)`;
          select.appendChild(opt);
        });
        this.currentFile = res.files[0].name;
      } else {
        select.innerHTML = '<option value="">Nessun foglio CUP trovato in data/cup/</option>';
      }
      this.loadPatients();
    } catch (err) {
      console.error("Errore caricamento file CUP:", err);
    }
  }

  static async loadPatients() {
    const tbody = document.getElementById("cup-patients-tbody");
    tbody.innerHTML = '<tr><td colspan="7" style="text-align: center; color: var(--text-muted); padding: 1.5rem;">Ricerca pazienti in corso...</td></tr>';

    const search = document.getElementById("cup-search-input").value.trim();
    const select = document.getElementById("cup-file-select");
    const file = select ? select.value : "";

    try {
      const res = await APIClient.getCupPatients({
        page: this.currentPage,
        page_size: this.pageSize,
        search: search || undefined,
        cup_file: file || undefined
      });

      this.renderTable(res);
    } catch (err) {
      tbody.innerHTML = `<tr><td colspan="7" style="text-align: center; color: var(--status-danger); padding: 1.5rem;">Errore caricamento: ${err.message}</td></tr>`;
    }
  }

  static renderTable(res) {
    const tbody = document.getElementById("cup-patients-tbody");
    tbody.innerHTML = "";

    const patients = res.items || [];
    if (patients.length === 0) {
      tbody.innerHTML = '<tr><td colspan="7" style="text-align: center; color: var(--text-muted); padding: 2rem;">Nessun paziente trovato per i criteri selezionati.</td></tr>';
      return;
    }

    patients.forEach(p => {
      const tr = document.createElement("tr");

      const fullName = `${p.cognome || ''} ${p.nome || ''}`.trim() || 'Sconosciuto';
      const dob = p.data_nascita || '-';
      const cf = p.codice_fiscale || '-';
      const idStr = p.priamo_id ? `Priamo: ${p.priamo_id}` : (p.id_anagrafica_paziente ? `MEF: ${p.id_anagrafica_paziente}` : '-');
      const comune = p.comune_nascita || '-';

      const inRedcap = p.in_redcap;
      const statusBadge = inRedcap
        ? '<span class="badge badge-success">✓ In REDCap</span>'
        : '<span class="badge badge-neutral">Da Elaborare</span>';

      tr.innerHTML = `
        <td style="font-weight: 600;">${fullName}</td>
        <td>${dob}</td>
        <td><code>${cf}</code></td>
        <td style="font-size: 0.8125rem;">${idStr}</td>
        <td>${comune}</td>
        <td>${statusBadge}</td>
        <td>
          <div style="display: flex; gap: 0.375rem;">
            <button type="button" class="btn btn-primary btn-sm btn-action-pipeline" data-id="${p.patient_id || ''}" data-name="${p.nome || ''}" data-surname="${p.cognome || ''}" data-dob="${p.data_nascita || ''}">
              ⚡ Pipeline
            </button>
            <button type="button" class="btn btn-secondary btn-sm btn-action-verify" data-name="${p.nome || ''}" data-surname="${p.cognome || ''}" data-dob="${p.data_nascita || ''}">
              🔍 Verifica
            </button>
          </div>
        </td>
      `;
      tbody.appendChild(tr);
    });

    // Attach row events
    tbody.querySelectorAll(".btn-action-pipeline").forEach(b => {
      b.addEventListener("click", () => {
        const id = b.getAttribute("data-id");
        const name = b.getAttribute("data-name");
        const surname = b.getAttribute("data-surname");
        const dob = b.getAttribute("data-dob");

        document.getElementById("pipe-patient-id").value = id;
        document.getElementById("pipe-name").value = name;
        document.getElementById("pipe-surname").value = surname;
        document.getElementById("pipe-dob").value = dob;

        TabManager.switchToTab("tab-btn-pipeline", "panel-pipeline");
      });
    });

    tbody.querySelectorAll(".btn-action-verify").forEach(b => {
      b.addEventListener("click", async () => {
        const name = b.getAttribute("data-name");
        const surname = b.getAttribute("data-surname");
        const dob = b.getAttribute("data-dob");
        b.textContent = "Verifica...";
        try {
          const v = await APIClient.verifyPriamoPatient({ name, surname, date_of_birth: dob });
          if (v.found) {
            alert(`Paziente TROVATO su Priamo HIS!\nID: ${v.patient_id}\nCF: ${v.codice_fiscale || 'N/D'}\nComune: ${v.comune_nascita || 'N/D'}`);
          } else {
            alert(`Paziente NON trovato su Priamo HIS per ${name} ${surname} (${dob}).`);
          }
        } catch (err) {
          alert(`Errore verifica Priamo: ${err.message}`);
        } finally {
          b.textContent = "🔍 Verifica";
        }
      });
    });

    // Pagination info
    const total = res.total || 0;
    const start = (this.currentPage - 1) * this.pageSize + 1;
    const end = Math.min(start + patients.length - 1, total);
    document.getElementById("cup-pagination-info").textContent = `Visualizzazione ${start}-${end} di ${total} pazienti`;
  }
}

// =============================================================================
// 5. Pipeline IA & Real-Time Monitoring Controller (SSE)
// =============================================================================
class PipelineRealtimeController {
  static currentEventSource = null;
  static taskStartTime = 0;
  static timerInterval = null;

  static init() {
    document.getElementById("btn-start-single-pipeline").addEventListener("click", () => this.startSingle());
    document.getElementById("btn-batch-modal-open").addEventListener("click", () => {
      document.getElementById("modal-batch").removeAttribute("hidden");
    });
    document.getElementById("btn-close-batch").addEventListener("click", () => {
      document.getElementById("modal-batch").setAttribute("hidden", "true");
    });
    document.getElementById("btn-cancel-batch").addEventListener("click", () => {
      document.getElementById("modal-batch").setAttribute("hidden", "true");
    });
    document.getElementById("btn-start-batch-confirm").addEventListener("click", () => this.startBatch());
    document.getElementById("btn-copy-json").addEventListener("click", () => this.copyJson());
  }

  static async startSingle() {
    const patientId = document.getElementById("pipe-patient-id").value.trim();
    const name = document.getElementById("pipe-name").value.trim();
    const surname = document.getElementById("pipe-surname").value.trim();
    const dob = document.getElementById("pipe-dob").value.trim();
    const autoUpload = document.getElementById("pipe-auto-upload").checked;
    const dryRun = document.getElementById("pipe-dry-run").checked;

    if (!patientId && (!name || !surname)) {
      alert("Inserisci almeno un ID Paziente oppure Nome e Cognome.");
      return;
    }

    this.resetLiveUI();
    this.appendLog("Invio richiesta di elaborazione asincrona al server...", "info");

    try {
      const res = await APIClient.processPatientAsync({
        patient_id: patientId || undefined,
        name: name || undefined,
        surname: surname || undefined,
        date_of_birth: dob || undefined,
        auto_upload: autoUpload,
        dry_run: dryRun
      });

      this.connectSSE(res.task_id);
    } catch (err) {
      this.appendLog(`Errore avvio task: ${err.message}`, "error");
      document.getElementById("pipeline-live-badge").className = "badge badge-danger";
      document.getElementById("pipeline-live-badge").textContent = "Errore Avvio";
    }
  }

  static async startBatch() {
    document.getElementById("modal-batch").setAttribute("hidden", "true");
    const limit = parseInt(document.getElementById("batch-limit-input").value, 10) || 5;
    const autoUpload = document.getElementById("pipe-auto-upload").checked;
    const dryRun = document.getElementById("pipe-dry-run").checked;

    this.resetLiveUI();
    this.appendLog(`Avvio batch per i primi ${limit} pazienti della coorte CUP...`, "info");

    try {
      const res = await APIClient.processBatchAsync({
        limit: limit,
        auto_upload: autoUpload,
        dry_run: dryRun
      });

      this.connectSSE(res.task_id);
    } catch (err) {
      this.appendLog(`Errore avvio batch: ${err.message}`, "error");
      document.getElementById("pipeline-live-badge").className = "badge badge-danger";
      document.getElementById("pipeline-live-badge").textContent = "Errore Avvio";
    }
  }

  static resetLiveUI() {
    if (this.currentEventSource) {
      this.currentEventSource.close();
      this.currentEventSource = null;
    }
    if (this.timerInterval) {
      clearInterval(this.timerInterval);
      this.timerInterval = null;
    }

    document.getElementById("pipeline-live-badge").className = "badge badge-info";
    document.getElementById("pipeline-live-badge").textContent = "In Esecuzione";
    document.getElementById("live-progress-fill").style.width = "0%";
    document.getElementById("live-percentage").textContent = "0%";
    document.getElementById("live-step-text").textContent = "Avvio task in background...";
    document.getElementById("live-task-id").textContent = "...";
    document.getElementById("live-elapsed-time").textContent = "0s";
    document.getElementById("pipeline-event-terminal").innerHTML = "";
    document.getElementById("live-doc-cards-grid").innerHTML = "";
    document.getElementById("live-docs-counter").textContent = "0 documenti";
    document.getElementById("result-json-wrapper").setAttribute("hidden", "true");

    this.taskStartTime = Date.now();
    this.timerInterval = setInterval(() => {
      const sec = Math.floor((Date.now() - this.taskStartTime) / 1000);
      document.getElementById("live-elapsed-time").textContent = `${sec}s`;
    }, 1000);
  }

  static connectSSE(taskId) {
    document.getElementById("live-task-id").textContent = taskId;
    const url = `/api/tasks/${taskId}/events`;
    this.appendLog(`Connessione streaming SSE stabilita con ${url}`, "info");

    const eventSource = new EventSource(url);
    this.currentEventSource = eventSource;

    eventSource.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        this.handleSSEUpdate(data);
      } catch (err) {
        console.error("Errore parsing payload SSE:", err);
      }
    };

    eventSource.onerror = (err) => {
      console.warn("Connessione SSE terminata o chiusa:", err);
      eventSource.close();
      this.currentEventSource = null;
      if (this.timerInterval) clearInterval(this.timerInterval);
    };
  }

  static handleSSEUpdate(data) {
    const pct = data.progress_pct || 0;
    document.getElementById("live-progress-fill").style.width = `${pct}%`;
    document.getElementById("live-percentage").textContent = `${pct}%`;

    if (data.current_step) {
      document.getElementById("live-step-text").textContent = data.current_step;
    }

    // Append new log lines from server
    if (data.events_log && Array.isArray(data.events_log)) {
      data.events_log.forEach(ev => {
        this.appendLogUnique(ev.message, ev.time);
      });
    }

    // Render processed documents cards
    if (data.processed_documents && Array.isArray(data.processed_documents)) {
      this.renderDocCards(data.processed_documents);
    }

    // Completion or failure handling
    if (data.status === "completed") {
      document.getElementById("pipeline-live-badge").className = "badge badge-success";
      document.getElementById("pipeline-live-badge").textContent = "✓ Completato";
      this.appendLog("✓ Task completato con successo!", "success");
      if (this.timerInterval) clearInterval(this.timerInterval);

      if (data.result) {
        this.renderResultJson(data.result);
      }
      OverviewController.load();
    } else if (data.status === "failed") {
      document.getElementById("pipeline-live-badge").className = "badge badge-danger";
      document.getElementById("pipeline-live-badge").textContent = "✗ Fallito";
      this.appendLog(`✗ Errore task: ${data.error || 'Errore sconosciuto'}`, "error");
      if (this.timerInterval) clearInterval(this.timerInterval);
    }
  }

  static appendLog(msg, type = "info") {
    const term = document.getElementById("pipeline-event-terminal");
    const timeStr = new Date().toTimeString().split(" ")[0];
    const line = document.createElement("div");
    line.className = "event-terminal-line";
    line.innerHTML = `
      <span class="event-time">[${timeStr}]</span>
      <span class="event-msg ${type}">${msg}</span>
    `;
    term.appendChild(line);
    term.scrollTop = term.scrollHeight;
  }

  static seenLogKeys = new Set();
  static appendLogUnique(msg, timeStr) {
    const key = `${timeStr}_${msg}`;
    if (this.seenLogKeys.has(key)) return;
    this.seenLogKeys.add(key);

    const term = document.getElementById("pipeline-event-terminal");
    const line = document.createElement("div");
    line.className = "event-terminal-line";
    const type = msg.includes("Errore") || msg.includes("✗") ? "error" : (msg.includes("Completato") || msg.includes("✓") ? "success" : "info");
    line.innerHTML = `
      <span class="event-time">[${timeStr}]</span>
      <span class="event-msg ${type}">${msg}</span>
    `;
    term.appendChild(line);
    term.scrollTop = term.scrollHeight;
  }

  static renderDocCards(docs) {
    const container = document.getElementById("live-doc-cards-grid");
    document.getElementById("live-docs-counter").textContent = `${docs.length} documenti`;
    container.innerHTML = "";

    docs.forEach(doc => {
      const card = document.createElement("div");
      card.className = "doc-card";

      const formName = doc.classified_form || (doc.forms ? doc.forms.join(", ") : "non specificato");
      const fieldCount = doc.fields_extracted_count || doc.fields || 0;
      const duration = doc.duration_sec ? `${doc.duration_sec}s` : "-";
      const statusClass = doc.status === "success" ? "badge-success" : (doc.status === "skipped" ? "badge-neutral" : "badge-danger");

      card.innerHTML = `
        <div class="doc-card-title">📄 ${doc.filename}</div>
        <div style="font-size: 0.8125rem; color: var(--primary-dark); margin-top: 0.25rem;">
          <strong>Target REDCap:</strong> <code>${formName}</code>
        </div>
        <div class="doc-card-meta">
          <span>Campi estratti: <strong>${fieldCount}</strong></span>
          <span>Tempo: ${duration}</span>
          <span class="badge ${statusClass}">${doc.status || 'success'}</span>
        </div>
      `;
      container.appendChild(card);
    });
  }

  static renderResultJson(result) {
    const pre = document.getElementById("pipeline-result-json");
    pre.textContent = JSON.stringify(result, null, 2);
    document.getElementById("result-json-wrapper").removeAttribute("hidden");
  }

  static copyJson() {
    const text = document.getElementById("pipeline-result-json").textContent;
    navigator.clipboard.writeText(text).then(() => {
      alert("JSON copiato negli appunti!");
    });
  }
}

// =============================================================================
// 6. Data Quality & Audit Controller
// =============================================================================
class AuditController {
  static async load() {
    const container = document.getElementById("audit-report-container");
    container.innerHTML = "Caricamento report qualità REDCap...";

    try {
      const report = await APIClient.getAuditReport();
      this.renderReport(report);
    } catch (err) {
      container.innerHTML = `<span style="color: var(--status-danger);">Impossibile generare report di audit: ${err.message}</span>`;
    }
  }

  static renderReport(report) {
    document.getElementById("audit-total-patients").textContent = report.total_patients_audited || 0;
    document.getElementById("audit-complete-records").textContent = report.fully_completed_records_count || 0;
    document.getElementById("audit-false-complete").textContent = report.false_complete_flags_count || 0;
    document.getElementById("audit-critical-anomalies").textContent = report.inconsistencies ? report.inconsistencies.length : 0;

    let html = "";
    if (report.inconsistencies && report.inconsistencies.length > 0) {
      html += '<h4 style="margin-bottom: 0.5rem; color: var(--status-danger);">⚠️ Anomalie e Incongruenze Rilevate:</h4><ul style="padding-left: 1.25rem; margin-bottom: 1.25rem;">';
      report.inconsistencies.forEach(inc => {
        html += `<li style="margin-bottom: 0.375rem;"><strong>[Paziente ${inc.patient_id}]</strong> ${inc.description || JSON.stringify(inc)}</li>`;
      });
      html += '</ul>';
    } else {
      html += '<div style="color: var(--status-success); margin-bottom: 1.25rem;">✓ Nessuna incongruenza critica riscontrata nei record analizzati.</div>';
    }

    if (report.form_completion_matrix) {
      html += '<h4 style="margin-bottom: 0.5rem;">Avanzamento Form per Strumento REDCap:</h4>';
      html += '<div class="table-wrapper"><table><thead><tr><th>Form REDCap</th><th>Completati</th><th>Incompleti</th><th>Non Iniziati</th></tr></thead><tbody>';
      for (const [form, counts] of Object.entries(report.form_completion_matrix)) {
        html += `<tr>
          <td><strong>${form}</strong></td>
          <td><span class="badge badge-success">${counts.complete || 0}</span></td>
          <td><span class="badge badge-warning">${counts.unverified || counts.incomplete || 0}</span></td>
          <td><span class="badge badge-neutral">${counts.not_started || 0}</span></td>
        </tr>`;
      }
      html += '</tbody></table></div>';
    }

    document.getElementById("audit-report-container").innerHTML = html;
  }
}

// =============================================================================
// 7. Sync & Riconciliazione Controller
// =============================================================================
class SyncController {
  static async load() {
    const tbody = document.getElementById("sync-patients-tbody");
    tbody.innerHTML = '<tr><td colspan="5" style="text-align: center; color: var(--text-muted); padding: 1.5rem;">Rilevamento pazienti con form incompleti...</td></tr>';

    try {
      const res = await APIClient.getIncompletePatients();
      const patients = res.patients || [];

      if (patients.length === 0) {
        tbody.innerHTML = '<tr><td colspan="5" style="text-align: center; color: var(--status-success); padding: 1.5rem;">✓ Tutti i pazienti hanno i form completati al 100%!</td></tr>';
        return;
      }

      tbody.innerHTML = "";
      patients.forEach(p => {
        const tr = document.createElement("tr");
        const formsBadge = (p.incomplete_forms || []).map(f => `<span class="badge badge-warning" style="margin-right: 0.25rem;">${f}</span>`).join("");
        tr.innerHTML = `
          <td><code>${p.record_id}</code></td>
          <td style="font-weight: 600;">${p.name || ''} ${p.surname || ''}</td>
          <td>${p.date_of_birth || '-'}</td>
          <td>${formsBadge}</td>
          <td>
            <button type="button" class="btn btn-primary btn-sm btn-sync-single" data-id="${p.record_id}">
              🔄 Sincronizza
            </button>
          </td>
        `;
        tbody.appendChild(tr);
      });

      tbody.querySelectorAll(".btn-sync-single").forEach(b => {
        b.addEventListener("click", () => {
          const id = b.getAttribute("data-id");
          document.getElementById("pipe-patient-id").value = id;
          TabManager.switchToTab("tab-btn-pipeline", "panel-pipeline");
        });
      });
    } catch (err) {
      tbody.innerHTML = `<tr><td colspan="5" style="text-align: center; color: var(--status-danger); padding: 1.5rem;">Errore: ${err.message}</td></tr>`;
    }
  }
}

// =============================================================================
// 8. REDCap Records Controller
// =============================================================================
class RedCapController {
  static async load() {
    const tbody = document.getElementById("redcap-records-tbody");
    tbody.innerHTML = '<tr><td colspan="5" style="text-align: center; color: var(--text-muted); padding: 1.5rem;">Caricamento record da REDCap...</td></tr>';

    try {
      const records = await APIClient.getRedCapRecords();
      if (!records || records.length === 0) {
        tbody.innerHTML = '<tr><td colspan="5" style="text-align: center; color: var(--text-muted); padding: 2rem;">Nessun record presente in REDCap.</td></tr>';
        return;
      }

      tbody.innerHTML = "";
      records.forEach(r => {
        const tr = document.createElement("tr");
        const fullName = `${r.surname || ''} ${r.name || ''}`.trim() || 'Paziente Anonimo';
        const dob = r.date_of_birth || '-';
        const completedCount = r.completed_forms_count || r.forms_completed || 0;

        tr.innerHTML = `
          <td><code>${r.record_id}</code></td>
          <td style="font-weight: 600;">${fullName}</td>
          <td>${dob}</td>
          <td>
            <span class="badge ${completedCount >= 8 ? 'badge-success' : 'badge-warning'}">
              ${completedCount}/8 form
            </span>
          </td>
          <td>
            <button type="button" class="btn btn-danger btn-sm btn-delete-record" data-id="${r.record_id}">
              🗑️ Elimina
            </button>
          </td>
        `;
        tbody.appendChild(tr);
      });

      tbody.querySelectorAll(".btn-delete-record").forEach(b => {
        b.addEventListener("click", async () => {
          const id = b.getAttribute("data-id");
          if (confirm(`Sei sicuro di voler eliminare definitivamente il record ${id} da REDCap?`)) {
            try {
              await APIClient.deleteRedCapRecord(id);
              alert(`Record ${id} eliminato.`);
              this.load();
              OverviewController.load();
            } catch (err) {
              alert(`Errore eliminazione record: ${err.message}`);
            }
          }
        });
      });
    } catch (err) {
      tbody.innerHTML = `<tr><td colspan="5" style="text-align: center; color: var(--status-danger); padding: 1.5rem;">Errore caricamento: ${err.message}</td></tr>`;
    }
  }
}

// =============================================================================
// 9. App Initialization & Global Event Listeners
// =============================================================================
document.addEventListener("DOMContentLoaded", () => {
  TabManager.init();
  PipelineRealtimeController.init();
  OverviewController.load();

  // Settings modal
  const modalSettings = document.getElementById("modal-settings");
  const inputKey = document.getElementById("settings-api-key-input");
  document.getElementById("btn-open-settings").addEventListener("click", () => {
    inputKey.value = APIClient.getApiKey();
    modalSettings.removeAttribute("hidden");
  });
  document.getElementById("btn-close-settings").addEventListener("click", () => {
    modalSettings.setAttribute("hidden", "true");
  });
  document.getElementById("btn-cancel-settings").addEventListener("click", () => {
    modalSettings.setAttribute("hidden", "true");
  });
  document.getElementById("btn-save-settings").addEventListener("click", () => {
    APIClient.setApiKey(inputKey.value);
    modalSettings.setAttribute("hidden", "true");
    OverviewController.load();
  });

  // Overview quick buttons
  document.getElementById("btn-refresh-overview").addEventListener("click", () => OverviewController.load());
  document.getElementById("btn-quick-cup-download").addEventListener("click", async () => {
    try {
      alert("Avvio download da Priamo CUP...");
      const res = await APIClient.downloadLatestCup();
      alert(`Download completato: ${res.file_name || 'Successo'}`);
      OverviewController.load();
    } catch (err) {
      alert(`Errore download CUP: ${err.message}`);
    }
  });
  document.getElementById("btn-quick-run-audit").addEventListener("click", () => {
    TabManager.switchToTab("tab-btn-audit", "panel-audit");
  });
  document.getElementById("btn-quick-goto-pipeline").addEventListener("click", () => {
    TabManager.switchToTab("tab-btn-pipeline", "panel-pipeline");
  });

  // CUP search & pagination
  document.getElementById("btn-cup-search-submit").addEventListener("click", () => {
    CupController.currentPage = 1;
    CupController.loadPatients();
  });
  document.getElementById("cup-search-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      CupController.currentPage = 1;
      CupController.loadPatients();
    }
  });
  document.getElementById("cup-file-select").addEventListener("change", () => {
    CupController.currentPage = 1;
    CupController.loadPatients();
  });
  document.getElementById("btn-cup-prev").addEventListener("click", () => {
    if (CupController.currentPage > 1) {
      CupController.currentPage--;
      CupController.loadPatients();
    }
  });
  document.getElementById("btn-cup-next").addEventListener("click", () => {
    CupController.currentPage++;
    CupController.loadPatients();
  });
  document.getElementById("btn-cup-download-remote").addEventListener("click", async () => {
    try {
      const res = await APIClient.downloadLatestCup();
      alert(`Download completato: ${res.file_name}`);
      CupController.loadFiles();
    } catch (err) {
      alert(`Errore download: ${err.message}`);
    }
  });
  document.getElementById("btn-cup-export-csv").addEventListener("click", () => {
    window.open("/api/cup/export?format=csv", "_blank");
  });
  document.getElementById("btn-cup-export-json").addEventListener("click", () => {
    window.open("/api/cup/export?format=json", "_blank");
  });

  // Audit triggers
  document.getElementById("btn-run-full-audit").addEventListener("click", () => AuditController.load());

  // Sync triggers
  document.getElementById("btn-trigger-sync").addEventListener("click", async () => {
    try {
      const res = await APIClient.triggerSync({ auto_upload: true });
      alert(`Sincronizzazione avviata in background (Task: ${res.task_id})`);
    } catch (err) {
      alert(`Errore sincronizzazione: ${err.message}`);
    }
  });

  // REDCap refresh
  document.getElementById("btn-refresh-redcap-records").addEventListener("click", () => RedCapController.load());
});
