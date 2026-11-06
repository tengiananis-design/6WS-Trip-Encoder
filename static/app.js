
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
const UPLOAD_CHUNK_SIZE = 3;
const MAX_FILE_SIZE = 50 * 1024 * 1024;

function showMessage(element, message, type = "") {
    element.textContent = message;
    element.className = \`message \${type}\`;
}
function escapeHTML(value) {
    return String(value ?? "").replace(/[&<>"']/g, character => ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
    })[character]);
}
async function apiRequest(url, options = {}) {
    const response = await fetch(url, options);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || \`Request failed: \${response.status}\`);
    return data;
}
async function checkSystem() {
    const statusText = document.getElementById("systemStatus");
    const dot = document.querySelector(".status-dot");
    try {
        const result = await apiRequest("/health");
        statusText.textContent = result.status === "online" ? "System Online" : "System Unavailable";
        dot.classList.toggle("online", result.status === "online");
    } catch (error) {
        statusText.textContent = "Connection Error";
        dot.classList.remove("online");
    }
}
selectButton.addEventListener("click", () => pdfInput.click());
pdfInput.addEventListener("change", () => {
    addSelectedFiles(pdfInput.files);
    pdfInput.value = "";
});
function addSelectedFiles(files) {
    const incoming = Array.from(files);
    const existing = new Set(selectedPDFs.map(file => \`\${file.name}:\${file.size}:\${file.lastModified}\`));
    incoming.forEach(file => {
        if (!file.name.toLowerCase().endsWith(".pdf")) return;
        if (file.size > MAX_FILE_SIZE) {
            showMessage(uploadMessage, \`\${file.name} exceeds 50 MB and was skipped.\`, "error");
            return;
        }
        const key = \`\${file.name}:\${file.size}:\${file.lastModified}\`;
        if (!existing.has(key)) selectedPDFs.push(file);
    });
    renderSelectedFiles();
}
function renderSelectedFiles() {
    selectedFiles.innerHTML = "";
    selectedPDFs.forEach((file, index) => {
        const item = document.createElement("div");
        item.className = "file-item";
        item.innerHTML = \`<span>\${escapeHTML(file.name)}</span><span>\${(file.size / 1024 / 1024).toFixed(2)} MB
            <button type="button" class="action-button" data-remove="\${index}">Remove</button></span>\`;
        selectedFiles.appendChild(item);
    });
    uploadButton.disabled = selectedPDFs.length === 0;
    uploadButton.textContent = selectedPDFs.length ? \`Upload \${selectedPDFs.length} PDF(s)\` : "Upload Documents";
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
    const files = [...selectedPDFs];
    let batchId = null, uploaded = 0;
    uploadButton.disabled = true;
    try {
        uploadButton.textContent = "Preparing batch...";
        const batch = await apiRequest("/api/batches", {method: "POST"});
        batchId = batch.batch_id;
        for (let offset = 0; offset < files.length; offset += UPLOAD_CHUNK_SIZE) {
            const chunk = files.slice(offset, offset + UPLOAD_CHUNK_SIZE);
            const data = new FormData();
            data.append("batch_id", batchId);
            chunk.forEach(file => data.append("files", file));
            uploadButton.textContent = \`Uploading \${Math.min(offset + chunk.length, files.length)} / \${files.length}...\`;
            const result = await apiRequest("/api/upload", {method: "POST", body: data});
            uploaded += result.uploaded_count || 0;
            showMessage(uploadMessage, \`Uploaded \${uploaded} of \${files.length} PDFs. Batch: \${batchId}\`, "success");
            if (result.rejected_files?.length) {
                showMessage(uploadMessage, result.rejected_files.map(item => \`\${item.filename}: \${item.reason}\`).join(" · "), "error");
            }
        }
        selectedPDFs = [];
        renderSelectedFiles();
        showMessage(uploadMessage, \`\${uploaded} PDF(s) uploaded. Batch: \${batchId}\`, "success");
        await loadBatches();
    } catch (error) {
        // Keep all files selected for a safe retry; server-side duplicate filenames
        // are unique and will not overwrite earlier uploads.
        showMessage(uploadMessage, \`Batch \${batchId || "not created"}: \${uploaded} PDF(s) uploaded. Retry remaining files after checking the batch. \${error.message}\`, "error");
        await loadBatches();
    } finally {
        uploadButton.disabled = selectedPDFs.length === 0;
        uploadButton.textContent = selectedPDFs.length ? \`Retry upload (\${selectedPDFs.length} selected)\` : "Upload Documents";
    }
}
refreshButton.addEventListener("click", loadBatches);
async function loadBatches() {
    batchList.innerHTML = \`<p class="empty-state">Loading batches...</p>\`;
    try {
        const result = await apiRequest("/api/batches");
        renderBatches(result.batches || []);
        const batches = result.batches || [];
        document.getElementById("batchCount").textContent = batches.length;
        document.getElementById("documentCount").textContent = batches.reduce((sum, item) => sum + (item.document_count || 0), 0);
    } catch (error) {
        batchList.innerHTML = \`<p class="empty-state">\${escapeHTML(error.message)}</p>\`;
    }
}
function renderBatches(batches) {
    batchList.innerHTML = "";
    if (!batches.length) {
        batchList.innerHTML = \`<p class="empty-state">No uploaded batches yet.</p>\`; return;
    }
    batches.forEach(batch => {
        const card = document.createElement("div");
        card.className = "batch-card";
        const files = (batch.documents || []).map(file => escapeHTML(file.filename)).join(", ");
        card.innerHTML = \`<div class="batch-header"><h3>Batch \${escapeHTML(batch.batch_id)}</h3>
            <span class="badge warning">\${escapeHTML(batch.status)}</span></div>
            <p class="batch-meta">\${batch.document_count} document(s) · \${escapeHTML(batch.created_at)}</p>
            <p class="batch-meta">\${files}</p><div class="batch-actions">
            <button class="action-button" data-analyze="\${escapeHTML(batch.batch_id)}">Analyze Documents</button>
            <button class="action-button" data-results="\${escapeHTML(batch.batch_id)}">View Results</button>
            <button class="action-button" data-approve="\${escapeHTML(batch.batch_id)}">Approve & Export</button></div>\`;
        batchList.appendChild(card);
    });
}
batchList.addEventListener("click", async event => {
    const a = event.target.closest("[data-analyze]");
    const v = event.target.closest("[data-results]");
    const p = event.target.closest("[data-approve]");
    try {
        if (a) await analyzeBatch(a.dataset.analyze);
        if (v) await loadResults(v.dataset.results);
        if (p) await approveBatch(p.dataset.approve);
    } catch (error) { alert(error.message); }
});
async function analyzeBatch(id) {
    if (!confirm("Start AI analysis for this batch?")) return;
    analysisResults.innerHTML = \`<p class="empty-state">Extracting PDFs concurrently and analyzing. Large batches may take a few minutes...</p>\`;
    const result = await apiRequest(\`/api/analyze/\${encodeURIComponent(id)}\`, {method: "POST"});
    renderAnalysis(result.result);
    alert("Analysis completed. Review the results carefully.");
}
async function loadResults(id) {
    renderAnalysis(await apiRequest(\`/api/results/\${encodeURIComponent(id)}\`));
}
function renderAnalysis(result) {
    const trips = result.trips || [], tolls = result.toll_reimbursements || [], parking = result.parking_reimbursements || [];
    let html = \`<div class="result-section"><h3>Driver Information</h3><div class="result-item">
        <p><strong>Driver:</strong> \${escapeHTML(result.driver?.name || "Unidentified")}</p>
        <p><strong>Documents:</strong> \${result.documents_processed || 0} / \${result.documents_received || result.documents_processed || 0}</p>
        <p><strong>Approval:</strong> \${escapeHTML(result.approval_status || "pending_review")}</p></div></div>
        <div class="result-section"><h3>Trip Records (\${trips.length})</h3>\`;
    trips.forEach((trip, i) => { html += \`<div class="result-item"><p><strong>Trip \${i+1}</strong></p>
        <p>Date: \${escapeHTML(trip.trip_date)}</p><p>Trip Ticket: \${escapeHTML(trip.trip_ticket_number)}</p>
        <p>Line Haul: \${escapeHTML(trip.line_haul_trip_number)}</p><p>Origin: \${escapeHTML(trip.origin)}</p>
        <p>Destination: \${escapeHTML(trip.destination)}</p><p>Segments: \${(trip.segments || []).length}</p>
        <p>Review Flags: \${escapeHTML((trip.review_flags || []).join("; ") || "None")}</p></div>\`; });
    html += \`</div><div class="result-section"><h3>Toll Reimbursements (\${tolls.length})</h3>\`;
    tolls.forEach(row => { html += \`<div class="result-item"><p>Date: \${escapeHTML(row.date)}</p><p>Location: \${escapeHTML(row.location)}</p><p>Amount: \${escapeHTML(row.amount)}</p></div>\`; });
    html += \`</div><div class="result-section"><h3>Parking Reimbursements (\${parking.length})</h3>\`;
    parking.forEach(row => { html += \`<div class="result-item"><p>Date: \${escapeHTML(row.date)}</p><p>Location: \${escapeHTML(row.location)}</p><p>Amount: \${escapeHTML(row.amount)}</p></div>\`; });
    html += \`</div><div class="result-section"><h3>Unresolved Items</h3><div class="result-item">\${escapeHTML((result.unresolved_items || []).join("; ") || "No unresolved items reported.")}</div></div>\`;
    analysisResults.innerHTML = html;
}
async function approveBatch(id) {
    const result = await apiRequest(\`/api/results/\${encodeURIComponent(id)}\`);
    if (result.overall_review_required !== false) {
        alert("This batch still requires review. Resolve all flagged items before export."); return;
    }
    if (!confirm("Have you reviewed and verified every record? Approval will export to Google Sheets.")) return;
    result.approval_status = "approved";
    await apiRequest(\`/api/results/\${encodeURIComponent(id)}\`, {
        method: "PUT", headers: {"Content-Type": "application/json"}, body: JSON.stringify(result)
    });
    const exported = await apiRequest(\`/api/approve/\${encodeURIComponent(id)}\`, {method: "POST"});
    alert(exported.message || "Export completed.");
}
async function initializeDashboard() { await Promise.all([checkSystem(), loadBatches()]); }
initializeDashboard();
