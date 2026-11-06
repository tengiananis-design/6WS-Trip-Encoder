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

function showMessage(element, message, type = "") { element.textContent = message; element.className = `message ${type}`; }
function escapeHTML(value) { return String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c])); }
async function apiRequest(url, options = {}) {
    const response = await fetch(url, options);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || `Request failed: ${response.status}`);
    return data;
}
async function checkSystem() {
    const statusText = document.getElementById("systemStatus");
    const dot = document.querySelector(".status-dot");
    try {
        const result = await apiRequest("/health");
        statusText.textContent = result.status === "online" ? "System Online" : "System Unavailable";
        dot.classList.toggle("online", result.status === "online");
    } catch { statusText.textContent = "Connection Error"; dot.classList.remove("online"); }
}
selectButton.addEventListener("click", () => pdfInput.click());
pdfInput.addEventListener("change", () => addSelectedFiles(pdfInput.files));
function addSelectedFiles(files) {
    const incoming = Array.from(files);
    const valid = incoming.filter(file => file.name.toLowerCase().endsWith(".pdf"));
    const invalid = incoming.length - valid.length;
    selectedPDFs = [...selectedPDFs, ...valid];
    showMessage(uploadMessage, invalid ? `${invalid} non-PDF file(s) ignored.` : "");
    renderSelectedFiles();
}
function renderSelectedFiles() {
    selectedFiles.innerHTML = "";
    selectedPDFs.forEach((file, index) => {
        const item = document.createElement("div"); item.className = "file-item";
        item.innerHTML = `<span>${escapeHTML(file.name)}</span><span>${(file.size/1048576).toFixed(2)} MB <button type="button" class="action-button" data-remove="${index}">Remove</button></span>`;
        selectedFiles.appendChild(item);
    });
    uploadButton.disabled = selectedPDFs.length === 0;
}
selectedFiles.addEventListener("click", event => {
    const button = event.target.closest("[data-remove]"); if (!button) return;
    selectedPDFs.splice(Number(button.dataset.remove), 1); renderSelectedFiles();
});
dropArea.addEventListener("dragover", event => { event.preventDefault(); dropArea.classList.add("dragover"); });
dropArea.addEventListener("dragleave", () => dropArea.classList.remove("dragover"));
dropArea.addEventListener("drop", event => { event.preventDefault(); dropArea.classList.remove("dragover"); addSelectedFiles(event.dataTransfer.files); });
uploadButton.addEventListener("click", uploadDocuments);
async function uploadDocuments() {
    if (!selectedPDFs.length) return;
    const form = new FormData(); selectedPDFs.forEach(file => form.append("files", file));
    uploadButton.disabled = true; uploadButton.textContent = "Uploading...";
    try {
        const result = await apiRequest("/api/upload", {method:"POST", body:form});
        showMessage(uploadMessage, `${result.document_count} PDF(s) uploaded. Batch: ${result.batch_id}`, "success");
        selectedPDFs = []; renderSelectedFiles(); await loadBatches();
    } catch (error) { showMessage(uploadMessage, error.message, "error"); }
    finally { uploadButton.disabled = selectedPDFs.length === 0; uploadButton.textContent = "Upload Documents"; }
}
refreshButton.addEventListener("click", loadBatches);
async function loadBatches() {
    batchList.innerHTML = '<p class="empty-state">Loading batches...</p>';
    try {
        const result = await apiRequest("/api/batches"); renderBatches(result.batches || []);
        document.getElementById("batchCount").textContent = result.batches?.length || 0;
        document.getElementById("documentCount").textContent = (result.batches || []).reduce((n,b)=>n+(b.document_count||0),0);
    } catch(error) { batchList.innerHTML = `<p class="empty-state">${escapeHTML(error.message)}</p>`; }
}
function renderBatches(batches) {
    batchList.innerHTML = "";
    if (!batches.length) { batchList.innerHTML = '<p class="empty-state">No uploaded batches yet.</p>'; return; }
    batches.forEach(batch => {
        const card = document.createElement("div"); card.className = "batch-card";
        card.innerHTML = `<div class="batch-header"><h3>Batch ${escapeHTML(batch.batch_id)}</h3><span class="badge warning">${escapeHTML(batch.status)}</span></div>
        <p class="batch-meta">${batch.document_count} document(s) · ${escapeHTML(batch.created_at)}</p>
        <p class="batch-meta">${(batch.documents||[]).map(f=>escapeHTML(f.filename)).join(", ")}</p>
        <details class="assignment-panel"><summary>Assign PDFs to drivers (optional)</summary>
        <p>For a toll, parking, or other PDF that does not show a driver's name, enter its stored filename and the correct driver name. Leave other entries blank.</p>
        <textarea data-assignments-input="${escapeHTML(batch.batch_id)}" rows="6" style="width:100%;font-family:monospace">${escapeHTML(JSON.stringify(Object.fromEntries((batch.documents||[]).map(f=>[f.filename,""])),null,2))}</textarea>
        <button class="action-button" data-save-assignments="${escapeHTML(batch.batch_id)}">Save Driver Assignments</button></details>
        <div class="batch-actions"><button class="action-button" data-analyze="${escapeHTML(batch.batch_id)}">Analyze Documents</button>
        <button class="action-button" data-results="${escapeHTML(batch.batch_id)}">View Results</button>
        <button class="action-button" data-approve="${escapeHTML(batch.batch_id)}">Approve & Export</button></div>`;
        batchList.appendChild(card);
    });
}
batchList.addEventListener("click", async event => {
    const a=event.target.closest("[data-analyze]"), r=event.target.closest("[data-results]"), p=event.target.closest("[data-approve]"), s=event.target.closest("[data-save-assignments]");
    try { if(s) await saveDriverAssignments(s.dataset.saveAssignments); if(a) await analyzeBatch(a.dataset.analyze); if(r) await loadResults(r.dataset.results); if(p) await approveBatch(p.dataset.approve); }
    catch(error) { alert(error.message); }
});
async function saveDriverAssignments(batchId) {
    const editor = Array.from(document.querySelectorAll("[data-assignments-input]"))
        .find(node => node.dataset.assignmentsInput === batchId);
    if (!editor) throw new Error("Assignment editor not found.");
    let assignments;
    try { assignments = JSON.parse(editor.value); }
    catch { throw new Error("Invalid assignment JSON. Check commas, quotes, and brackets."); }
    if (!assignments || typeof assignments !== "object" || Array.isArray(assignments)) {
        throw new Error("Assignments must be a JSON object mapping filenames to driver names.");
    }
    const filled = Object.fromEntries(Object.entries(assignments).filter(([,name]) => String(name||"").trim()));
    const result = await apiRequest(`/api/assignments/${encodeURIComponent(batchId)}`, {
        method:"PUT",
        headers:{"Content-Type":"application/json"},
        body:JSON.stringify({assignments:filled})
    });
    alert(result.message + ` (${result.assignment_count} assignment(s) saved.)`);
}

async function analyzeBatch(batchId) {
    if (!confirm("Start AI analysis for this batch?")) return;
    analysisResults.innerHTML = '<div class="result-section"><h3>Processing documents</h3><p id="jobProgressText">Queueing analysis...</p><progress id="jobProgressBar" max="100" value="0" style="width:100%"></progress></div>';
    await apiRequest(`/api/analyze/${encodeURIComponent(batchId)}`, {method:"POST"});
    await pollAnalysisJob(batchId);
}
async function pollAnalysisJob(batchId) {
    while (true) {
        const job = await apiRequest(`/api/jobs/${encodeURIComponent(batchId)}`);
        const total = Number(job.total_documents || 0);
        const done = Number(job.completed_documents || 0);
        const textNode = document.getElementById("jobProgressText");
        const bar = document.getElementById("jobProgressBar");
        if (textNode) textNode.textContent = `${job.status || "queued"} — ${done}/${total} PDFs processed${job.current_filename ? " · " + job.current_filename : ""}`;
        if (bar) bar.value = total ? Math.round(done / total * 100) : 0;
        if (job.status === "completed") { await loadResults(batchId); return; }
        if (job.status === "failed") throw new Error(job.error || "Analysis failed.");
        await new Promise(resolve => setTimeout(resolve, 2500));
    }
}
async function loadResults(batchId) { renderAnalysis(await apiRequest(`/api/results/${encodeURIComponent(batchId)}`)); }
function renderAnalysis(result) {
    const trips=result.trips||[], tolls=result.toll_reimbursements||[], parking=result.parking_reimbursements||[];
    let html=`<div class="result-section"><h3>Driver Information</h3><div class="result-item"><p><strong>Driver:</strong> ${escapeHTML(result.driver?.name||"Unidentified")}</p>
    <p><strong>Documents:</strong> ${result.documents_processed||0}</p><p><strong>Approval:</strong> ${escapeHTML(result.approval_status||"pending_review")}</p>
    <p><strong>Review required:</strong> ${result.overall_review_required===false?"No":"Yes"}</p></div></div>
    <div class="result-section"><h3>Driver Groups (${(result.driver_groups||[]).length})</h3>
    ${(result.driver_groups||[]).map(g=>`<div class="result-item"><p><strong>${escapeHTML(g.driver?.name||"Unidentified")}</strong> — ${(g.documents||[]).length} document(s)</p><p>Confidence: ${escapeHTML(g.driver?.confidence||"unknown")}</p><p>Trips: ${(g.trips||[]).length} · Tolls: ${(g.toll_reimbursements||[]).length} · Parking: ${(g.parking_reimbursements||[]).length}</p></div>`).join("") || '<div class="result-item">No confirmed driver groups.</div>'}
    </div><div class="result-section"><h3>Trip Records (${trips.length})</h3>`;
    trips.forEach((t,i)=>{html+=`<div class="result-item"><p><strong>Trip ${i+1}</strong></p><p>Driver: ${escapeHTML(t.driver_name||result.driver?.name||"Unassigned")}</p><p>Date: ${escapeHTML(t.trip_date)}</p><p>Ticket: ${escapeHTML(t.trip_ticket_number)}</p><p>Line Haul: ${escapeHTML(t.line_haul_trip_number)}</p><p>Origin: ${escapeHTML(t.origin)}</p><p>Drop offs: ${escapeHTML((t.drop_offs||[]).join(" → "))}</p><p>Destination: ${escapeHTML(t.destination)}</p><p>Segments: ${(t.segments||[]).length}</p><p>Flags: ${escapeHTML((t.review_flags||[]).join("; ")||"None")}</p></div>`;});
    html+='</div><div class="result-section"><h3>Toll Reimbursements ('+tolls.length+')</h3>';
    tolls.forEach(x=>{html+=`<div class="result-item"><p>Driver: ${escapeHTML(x.driver_name||"Unassigned")}</p><p>Date: ${escapeHTML(x.date)}</p><p>Location: ${escapeHTML(x.location)}</p><p>Amount: ${escapeHTML(x.amount)}</p><p>Flags: ${escapeHTML((x.review_flags||[]).join("; "))}</p></div>`;});
    html+='</div><div class="result-section"><h3>Parking Reimbursements ('+parking.length+')</h3>';
    parking.forEach(x=>{html+=`<div class="result-item"><p>Date: ${escapeHTML(x.date)}</p><p>Location: ${escapeHTML(x.location)}</p><p>Amount: ${escapeHTML(x.amount)}</p><p>Flags: ${escapeHTML((x.review_flags||[]).join("; "))}</p></div>`;});
    if(result.partial_export){
        const partial=result.partial_export;
        html+=`</div><div class="result-section"><h3>Export reconciliation required</h3><div class="result-item"><p><strong>Automatic retry blocked.</strong> Check the spreadsheet before attempting any manual correction.</p><p>Failed sheet: ${escapeHTML(partial.failed_sheet||"Unknown")}</p><p>Sheets that may already contain rows: ${escapeHTML((partial.completed_exports||[]).map(x=>x.sheet+" ("+x.rows_added+" rows)").join(", ")||"None confirmed")}</p><p>Error: ${escapeHTML(result.export_error||"Unknown export error")}</p></div></div>`;
    }
    html+='</div><div class="result-section"><h3>Unresolved Items</h3><div class="result-item">'+escapeHTML((result.unresolved_items||[]).map(x=>typeof x==="string"?x:JSON.stringify(x)).join("; ")||"None reported.")+'</div></div>';
    const batchId = result.batch_id || "";
    html += `</div><div class="result-section"><h3>Human Review Editor</h3>
    <p>Edit the JSON below to correct trip fields, driver grouping, reimbursements, or unresolved flags. Keep the JSON valid. Saving does not approve or export.</p>
    <textarea id="reviewJson" rows="18" style="width:100%;font-family:monospace">${escapeHTML(JSON.stringify(result,null,2))}</textarea>
    <button class="action-button" data-save-review="${escapeHTML(batchId)}">Save reviewed records</button>
    <span id="reviewSaveMessage" role="status"></span></div>`;
    analysisResults.innerHTML=html;
}
async function approveBatch(batchId) {
    const result=await apiRequest(`/api/results/${encodeURIComponent(batchId)}`);
    if(result.overall_review_required!==false){alert("This batch still has unresolved review flags. Resolve them before approval.");return;}
    if(!confirm("Have you personally reviewed and verified every record? Approval will export the data to Google Sheets."))return;
    const response=await apiRequest(`/api/approve/${encodeURIComponent(batchId)}`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({review_confirmed:true})});
    alert(response.message||"Export completed."); await loadResults(batchId);
}
async function initializeDashboard(){await checkSystem();await loadBatches();}
initializeDashboard();

analysisResults.addEventListener("click", async event => {
    const button = event.target.closest("[data-save-review]");
    if (!button) return;
    const batchId = button.dataset.saveReview;
    const message = document.getElementById("reviewSaveMessage");
    try {
        const reviewed = JSON.parse(document.getElementById("reviewJson").value);
        const saved = await apiRequest(`/api/review/${encodeURIComponent(batchId)}`, {
            method:"PUT",
            headers:{"Content-Type":"application/json"},
            body:JSON.stringify({result:reviewed})
        });
        message.textContent = saved.message + (saved.overall_review_required ? " Outstanding review flags remain." : " Ready for explicit approval.");
        message.className = saved.overall_review_required ? "message warning" : "message success";
        await loadResults(batchId);
    } catch(error) {
        message.textContent = error instanceof SyntaxError ? "Invalid JSON. Check commas, quotes, and brackets." : error.message;
        message.className = "message error";
    }
});
