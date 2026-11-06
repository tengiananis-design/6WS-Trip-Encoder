const pdfInput = document.getElementById("pdfFiles");
const selectButton = document.getElementById("selectFilesButton");
const uploadButton = document.getElementById("uploadButton");
const selectedFiles = document.getElementById("selectedFiles");
const uploadMessage = document.getElementById("uploadMessage");
const batchList = document.getElementById("batchList");
const analysisResults = document.getElementById("analysisResults");
const refreshButton = document.getElementById("refreshButton");
const dropArea = document.getElementById("dropArea");

let selectedPDFs = [];
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

function showMessage(element, message, type = "") {
    element.textContent = message;
    element.className = `message ${type}`;
}
function escapeHTML(value) {
    const safe = value && typeof value === "object" ? JSON.stringify(value) : value;
    return String(safe ?? "").replace(/[&<>"']/g, character => ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
    })[character]);
}
async function apiRequest(url, options = {}) {
    const response = await fetch(url, options);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail || `Request failed: ${response.status}`));
    return data;
}
async function checkSystem() {
    const statusText = document.getElementById("systemStatus");
    const dot = document.querySelector(".status-dot");
    try {
        const result = await apiRequest("/health");
        statusText.textContent = result.status === "online" ? "System Online" : "System Unavailable";
        dot.classList.toggle("online", result.status === "online");
    } catch {
        statusText.textContent = "Connection Error";
        dot.classList.remove("online");
    }
}

selectButton.addEventListener("click", () => pdfInput.click());
pdfInput.addEventListener("change", () => addSelectedFiles(pdfInput.files));
function addSelectedFiles(files) {
    const incoming = Array.from(files);
    const valid = incoming.filter(file => file.name.toLowerCase().endsWith(".pdf") && (!file.type || file.type === "application/pdf"));
    const invalid = incoming.length - valid.length;
    selectedPDFs = [...selectedPDFs, ...valid];
    showMessage(uploadMessage, invalid ? `${invalid} non-PDF file(s) were ignored.` : "", invalid ? "error" : "");
    renderSelectedFiles();
}
function renderSelectedFiles() {
    selectedFiles.innerHTML = "";
    selectedPDFs.forEach((file, index) => {
        const item = document.createElement("div");
        item.className = "file-item";
        item.innerHTML = `<span>${escapeHTML(file.name)}</span><span>${(file.size / 1048576).toFixed(2)} MB <button type="button" class="action-button" data-remove="${index}">Remove</button></span>`;
        selectedFiles.appendChild(item);
    });
    uploadButton.disabled = selectedPDFs.length === 0;
}
selectedFiles.addEventListener("click", event => {
    const button = event.target.closest("[data-remove]");
    if (!button) return;
    selectedPDFs.splice(Number(button.dataset.remove), 1);
    renderSelectedFiles();
});
dropArea.addEventListener("dragover", event => { event.preventDefault(); dropArea.classList.add("dragover"); });
dropArea.addEventListener("dragleave", () => dropArea.classList.remove("dragover"));
dropArea.addEventListener("drop", event => {
    event.preventDefault(); dropArea.classList.remove("dragover"); addSelectedFiles(event.dataTransfer.files);
});

uploadButton.addEventListener("click", uploadDocuments);
async function uploadDocuments() {
    if (!selectedPDFs.length) return;
    const formData = new FormData();
    selectedPDFs.forEach(file => formData.append("files", file));
    uploadButton.disabled = true; uploadButton.textContent = "Uploading...";
    try {
        const result = await apiRequest("/api/upload", {method: "POST", body: formData});
        showMessage(uploadMessage, `${result.document_count} PDF(s) uploaded. Batch: ${result.batch_id}`, "success");
        selectedPDFs = []; renderSelectedFiles(); await loadBatches();
    } catch (error) {
        showMessage(uploadMessage, error.message, "error");
    } finally {
        uploadButton.disabled = selectedPDFs.length === 0; uploadButton.textContent = "Upload Documents";
    }
}

refreshButton.addEventListener("click", loadBatches);
async function loadBatches() {
    batchList.innerHTML = '<p class="empty-state">Loading batches...</p>';
    try {
        const result = await apiRequest("/api/batches");
        const batches = result.batches || [];
        renderBatches(batches);
        document.getElementById("batchCount").textContent = batches.length;
        document.getElementById("documentCount").textContent = batches.reduce((sum, batch) => sum + (batch.document_count || 0), 0);
    } catch (error) {
        batchList.innerHTML = `<p class="empty-state">${escapeHTML(error.message)}</p>`;
    }
}
function renderBatches(batches) {
    batchList.innerHTML = "";
    if (!batches.length) {
        batchList.innerHTML = '<p class="empty-state">No uploaded batches yet.</p>'; return;
    }
    batches.forEach(batch => {
        const card = document.createElement("div");
        card.className = "batch-card";
        const files = (batch.documents || []).map(file => escapeHTML(file.filename)).join(", ");
        const running = ["queued", "processing"].includes(batch.status);
        card.innerHTML = `
          <div class="batch-header"><h3>Batch ${escapeHTML(batch.batch_id)}</h3><span class="badge warning">${escapeHTML(batch.status)}</span></div>
          <p class="batch-meta">${batch.document_count} document(s) · ${escapeHTML(batch.created_at)}</p>
          <p class="batch-meta">${files}</p>
          <div class="batch-actions">
            <button class="action-button" data-analyze="${escapeHTML(batch.batch_id)}" ${running || batch.status === "completed" ? "disabled" : ""}>Analyze Documents</button>
            <button class="action-button" data-results="${escapeHTML(batch.batch_id)}">View Status / Results</button>
            <button class="action-button" disabled title="Export is not enabled yet">Export (not enabled)</button>
          </div>`;
        batchList.appendChild(card);
    });
}
batchList.addEventListener("click", async event => {
    const analyze = event.target.closest("[data-analyze]");
    const view = event.target.closest("[data-results]");
    try {
        if (analyze) await analyzeBatch(analyze.dataset.analyze);
        if (view) await loadResults(view.dataset.results);
    } catch (error) { alert(error.message); }
});

async function analyzeBatch(batchId) {
    if (!confirm("Queue AI analysis for this batch? You can leave this page while the server processes it.")) return;
    analysisResults.innerHTML = '<p class="empty-state">Queueing analysis...</p>';
    await apiRequest(`/api/analyze/${encodeURIComponent(batchId)}`, {method: "POST"});
    await loadBatches();
    await pollAnalysis(batchId);
}
async function pollAnalysis(batchId) {
    analysisResults.innerHTML = '<p class="empty-state">Analysis queued. Waiting for the worker...</p>';
    while (true) {
        const state = await apiRequest(`/api/results/${encodeURIComponent(batchId)}`);
        if (state.status === "completed" && state.result) {
            renderAnalysis(state.result);
            await loadBatches();
            return;
        }
        if (state.status === "failed") {
            analysisResults.innerHTML = `<div class="result-item"><h3>Analysis failed</h3><p>${escapeHTML(state.error || "Unknown error")}</p></div>`;
            await loadBatches();
            return;
        }
        analysisResults.innerHTML = `<p class="empty-state">Analysis ${escapeHTML(state.status || "pending")}... This page checks for results every 5 seconds.</p>`;
        await sleep(5000);
    }
}
async function loadResults(batchId) {
    const state = await apiRequest(`/api/results/${encodeURIComponent(batchId)}`);
    if (state.status === "completed" && state.result) renderAnalysis(state.result);
    else if (state.status === "failed") analysisResults.innerHTML = `<div class="result-item"><h3>Analysis failed</h3><p>${escapeHTML(state.error || "Unknown error")}</p></div>`;
    else await pollAnalysis(batchId);
}

function renderAnalysis(result) {
    const drivers = result.drivers || [];
    const trips = result.trips || [];
    const tolls = result.toll_reimbursements || [];
    const parking = result.parking_reimbursements || [];
    const passway = result.passway_reimbursements || [];
    const errors = result.extraction_errors || [];
    let html = `
      <div class="result-section">
        <h3>Batch Review Status</h3>
        <div class="result-item">
          <p><strong>Documents:</strong> ${result.documents_processed || 0} / ${result.documents_total || 0} analyzed</p>
          <p><strong>Drivers identified:</strong> ${result.driver_count || 0}</p>
          <p><strong>Approval:</strong> ${escapeHTML(result.approval_status || "pending_review")}</p>
          <p><strong>Export:</strong> Not enabled — review and approval safeguards are still being built.</p>
        </div>
      </div>
      <div class="result-section"><h3>Driver Groups (${drivers.length})</h3>`;
    drivers.forEach(driver => {
        const name = driver.driver?.name || "Unidentified";
        html += `<div class="result-item"><h4>${escapeHTML(name)}</h4>
          <p>Documents: ${(driver.documents || []).length} · Trips: ${(driver.trips || []).length}</p>
          <p>Toll: ${(driver.toll_reimbursements || []).length} · Parking: ${(driver.parking_reimbursements || []).length} · Passway: ${(driver.passway_reimbursements || []).length}</p>
          <p>Status: ${escapeHTML(driver.status || "pending_review")}</p></div>`;
    });
    html += `</div><div class="result-section"><h3>Trip Summary Rows (${trips.length})</h3>`;
    trips.forEach((trip, i) => {
        html += `<div class="result-item"><h4>Trip ${i + 1}: ${escapeHTML(trip.driver_name || "")}</h4>
          <p>Date: ${escapeHTML(trip.trip_date)} · Linehaul: ${escapeHTML(trip.line_haul_trip_number)}</p>
          <p>Route: ${escapeHTML(trip.origin)} → ${escapeHTML((trip.drop_offs || []).join(" / "))} → ${escapeHTML(trip.destination)}</p>
          <p>ID: ${escapeHTML(trip.driver_id)} · Plate: ${escapeHTML(trip.plate_no)} · Fleet: ${escapeHTML(trip.fleet_type)}</p>
          <p>Check-in: ${escapeHTML(trip.check_in_time_actual)} · Docking: ${escapeHTML(trip.docking_time)} · Check-out: ${escapeHTML(trip.check_out_time)}</p>
          <p>Segments: ${(trip.segments || []).length}</p>
          <p>Review flags: ${escapeHTML((trip.review_flags || []).join("; ") || "None")}</p></div>`;
    });
    const renderRecords = (title, rows, fields) => {
        html += `</div><div class="result-section"><h3>${title} (${rows.length})</h3>`;
        rows.forEach(row => {
            html += '<div class="result-item">';
            fields.forEach(([label, key]) => { html += `<p><strong>${label}:</strong> ${escapeHTML(row[key])}</p>`; });
            html += `<p><strong>Review:</strong> ${escapeHTML((row.review_flags || []).join("; ") || "None")}</p></div>`;
        });
    };
    renderRecords("Toll reimbursement rows", tolls, [["Date","date"],["Linehaul","line_haul_trip_number"],["Driver","driver_name"],["RFID/provider","rfid_toll_charges"],["Entry","entry_point"],["Exit","exit_point"],["Tag","tag_number"],["Amount","amount"]]);
    renderRecords("Parking reimbursement rows", parking, [["Trip date","trip_date"],["Linehaul","line_haul_trip_number"],["Driver","driver_name"],["Vendor","vendor_name"],["Address","address"],["Tag","tag_number"],["Amount","amount"]]);
    renderRecords("Passway reimbursement rows", passway, [["Trip date","trip_date"],["Linehaul","line_haul_trip_number"],["Driver","driver_name"],["Vendor","vendor_name"],["Address","address"],["Tag","tag_number"],["Amount","amount"]]);
    html += `</div><div class="result-section"><h3>Unidentified / Conflicting Documents</h3><div class="result-item">`;
    const reviewItems = (result.grouping?.review_items || []).map(item => `${item.filename}: ${item.reason}`);
    html += escapeHTML(reviewItems.concat(errors.map(x => `${x.filename}: ${x.error}`)).join("; ") || "No extraction or identity conflicts reported.");
    html += `</div></div><div class="result-section"><h3>Unresolved Items</h3><div class="result-item">${escapeHTML(result.unresolved_items || [])}</div></div>`;
    analysisResults.innerHTML = html;
}

async function initializeDashboard() { await checkSystem(); await loadBatches(); }
initializeDashboard();
