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
        <div class="batch-actions"><button class="action-button" data-analyze="${escapeHTML(batch.batch_id)}">Analyze Documents</button>
        <button class="action-button" data-results="${escapeHTML(batch.batch_id)}">View Results</button>
        <button class="action-button" data-approve="${escapeHTML(batch.batch_id)}">Approve & Export</button></div>`;
        batchList.appendChild(card);
    });
}
batchList.addEventListener("click", async event => {
    const a=event.target.closest("[data-analyze]"), r=event.target.closest("[data-results]"), p=event.target.closest("[data-approve]");
    try { if(a) await analyzeBatch(a.dataset.analyze); if(r) await loadResults(r.dataset.results); if(p) await approveBatch(p.dataset.approve); }
    catch(error) { alert(error.message); }
});
async function analyzeBatch(batchId) {
    if (!confirm("Start AI analysis for this batch?")) return;
    analysisResults.innerHTML = '<p class="empty-state">AI analysis is running. Please wait...</p>';
    const result = await apiRequest(`/api/analyze/${encodeURIComponent(batchId)}`, {method:"POST"});
    renderAnalysis(result.result); alert("Analysis completed. Review all records carefully.");
}
async function loadResults(batchId) { renderAnalysis(await apiRequest(`/api/results/${encodeURIComponent(batchId)}`)); }
function renderAnalysis(result) {
    const trips=result.trips||[], tolls=result.toll_reimbursements||[], parking=result.parking_reimbursements||[];
    let html=`<div class="result-section"><h3>Driver Information</h3><div class="result-item"><p><strong>Driver:</strong> ${escapeHTML(result.driver?.name||"Unidentified")}</p>
    <p><strong>Documents:</strong> ${result.documents_processed||0}</p><p><strong>Approval:</strong> ${escapeHTML(result.approval_status||"pending_review")}</p>
    <p><strong>Review required:</strong> ${result.overall_review_required===false?"No":"Yes"}</p></div></div><div class="result-section"><h3>Trip Records (${trips.length})</h3>`;
    trips.forEach((t,i)=>{html+=`<div class="result-item"><p><strong>Trip ${i+1}</strong></p><p>Date: ${escapeHTML(t.trip_date)}</p><p>Ticket: ${escapeHTML(t.trip_ticket_number)}</p><p>Line Haul: ${escapeHTML(t.line_haul_trip_number)}</p><p>Origin: ${escapeHTML(t.origin)}</p><p>Drop offs: ${escapeHTML((t.drop_offs||[]).join(" → "))}</p><p>Destination: ${escapeHTML(t.destination)}</p><p>Segments: ${(t.segments||[]).length}</p><p>Flags: ${escapeHTML((t.review_flags||[]).join("; ")||"None")}</p></div>`;});
    html+='</div><div class="result-section"><h3>Toll Reimbursements ('+tolls.length+')</h3>';
    tolls.forEach(x=>{html+=`<div class="result-item"><p>Date: ${escapeHTML(x.date)}</p><p>Location: ${escapeHTML(x.location)}</p><p>Amount: ${escapeHTML(x.amount)}</p><p>Flags: ${escapeHTML((x.review_flags||[]).join("; "))}</p></div>`;});
    html+='</div><div class="result-section"><h3>Parking Reimbursements ('+parking.length+')</h3>';
    parking.forEach(x=>{html+=`<div class="result-item"><p>Date: ${escapeHTML(x.date)}</p><p>Location: ${escapeHTML(x.location)}</p><p>Amount: ${escapeHTML(x.amount)}</p><p>Flags: ${escapeHTML((x.review_flags||[]).join("; "))}</p></div>`;});
    html+='</div><div class="result-section"><h3>Unresolved Items</h3><div class="result-item">'+escapeHTML((result.unresolved_items||[]).map(x=>typeof x==="string"?x:JSON.stringify(x)).join("; ")||"None reported.")+'</div></div>';
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
