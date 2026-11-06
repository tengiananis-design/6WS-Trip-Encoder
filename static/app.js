
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

// --------------------------------------------------
// GENERAL HELPERS
// --------------------------------------------------

function showMessage(element, message, type = "") {
    element.textContent = message;
    element.className = `message ${type}`;
}

function escapeHTML(value) {
    return String(value ?? "").replace(/[&<>"']/g, character => ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;"
    })[character]);
}

async function apiRequest(url, options = {}) {
    const response = await fetch(url, options);

    const data = await response.json().catch(() => ({}));

    if (!response.ok) {
        throw new Error(
            data.detail || `Request failed: ${response.status}`
        );
    }

    return data;
}

// --------------------------------------------------
// SYSTEM STATUS
// --------------------------------------------------

async function checkSystem() {
    const statusText = document.getElementById("systemStatus");
    const dot = document.querySelector(".status-dot");

    try {
        const result = await apiRequest("/health");

        statusText.textContent = result.status === "online"
            ? "System Online"
            : "System Unavailable";

        dot.classList.toggle(
            "online",
            result.status === "online"
        );

    } catch (error) {
        statusText.textContent = "Connection Error";
        dot.classList.remove("online");
    }
}

// --------------------------------------------------
// FILE SELECTION
// --------------------------------------------------

selectButton.addEventListener("click", () => {
    pdfInput.click();
});

pdfInput.addEventListener("change", () => {
    addSelectedFiles(pdfInput.files);
});

function addSelectedFiles(files) {
    const incomingFiles = Array.from(files);

    const validFiles = incomingFiles.filter(file =>
        file.name.toLowerCase().endsWith(".pdf") &&
        file.type === "application/pdf"
    );

    const invalidCount = incomingFiles.length - validFiles.length;

    selectedPDFs = [
        ...selectedPDFs,
        ...validFiles
    ];

    if (invalidCount > 0) {
        showMessage(
            uploadMessage,
            `${invalidCount} non-PDF file(s) were ignored.`,
            "error"
        );
    } else {
        showMessage(uploadMessage, "");
    }

    renderSelectedFiles();
}

function renderSelectedFiles() {
    selectedFiles.innerHTML = "";

    selectedPDFs.forEach((file, index) => {
        const item = document.createElement("div");
        item.className = "file-item";

        item.innerHTML = `
            <span>${escapeHTML(file.name)}</span>
            <span>${(file.size / 1024 / 1024).toFixed(2)} MB
                <button type="button"
                    class="action-button"
                    data-remove="${index}">
                    Remove
                </button>
            </span>
        `;

        selectedFiles.appendChild(item);
    });

    uploadButton.disabled = selectedPDFs.length === 0;
}

selectedFiles.addEventListener("click", event => {
    const button = event.target.closest("[data-remove]");

    if (!button) return;

    const index = Number(button.dataset.remove);

    selectedPDFs.splice(index, 1);

    renderSelectedFiles();
});

// --------------------------------------------------
// DRAG AND DROP
// --------------------------------------------------

dropArea.addEventListener("dragover", event => {
    event.preventDefault();
    dropArea.classList.add("dragover");
});

dropArea.addEventListener("dragleave", () => {
    dropArea.classList.remove("dragover");
});

dropArea.addEventListener("drop", event => {
    event.preventDefault();
    dropArea.classList.remove("dragover");

    addSelectedFiles(event.dataTransfer.files);
});

// --------------------------------------------------
// UPLOAD
// --------------------------------------------------

uploadButton.addEventListener("click", uploadDocuments);

async function uploadDocuments() {
    if (selectedPDFs.length === 0) return;

    const formData = new FormData();

    selectedPDFs.forEach(file => {
        formData.append("files", file);
    });

    uploadButton.disabled = true;
    uploadButton.textContent = "Uploading...";

    try {
        const result = await apiRequest("/api/upload", {
            method: "POST",
            body: formData
        });

        showMessage(
            uploadMessage,
            `${result.document_count} PDF(s) uploaded successfully. Batch: ${result.batch_id}`,
            "success"
        );

        selectedPDFs = [];
        renderSelectedFiles();

        await loadBatches();

    } catch (error) {
        showMessage(
            uploadMessage,
            error.message,
            "error"
        );

    } finally {
        uploadButton.disabled = selectedPDFs.length === 0;
        uploadButton.textContent = "Upload Documents";
    }
}

// --------------------------------------------------
// BATCH INVENTORY
// --------------------------------------------------

refreshButton.addEventListener("click", loadBatches);

async function loadBatches() {
    batchList.innerHTML = `
        <p class="empty-state">Loading batches...</p>
    `;

    try {
        const result = await apiRequest("/api/batches");

        renderBatches(result.batches || []);

        const batchCount = result.batches?.length || 0;

        const documentCount = (result.batches || [])
            .reduce((sum, batch) =>
                sum + (batch.document_count || 0), 0
            );

        document.getElementById("batchCount").textContent =
            batchCount;

        document.getElementById("documentCount").textContent =
            documentCount;

    } catch (error) {
        batchList.innerHTML = `
            <p class="empty-state">
                ${escapeHTML(error.message)}
            </p>
        `;
    }
}

function renderBatches(batches) {
    batchList.innerHTML = "";

    if (batches.length === 0) {
        batchList.innerHTML = `
            <p class="empty-state">
                No uploaded batches yet.
            </p>
        `;

        return;
    }

    batches.forEach(batch => {
        const card = document.createElement("div");
        card.className = "batch-card";

        const files = (batch.documents || [])
            .map(file => escapeHTML(file.filename))
            .join(", ");

        card.innerHTML = `
            <div class="batch-header">
                <h3>Batch ${escapeHTML(batch.batch_id)}</h3>
                <span class="badge warning">
                    ${escapeHTML(batch.status)}
                </span>
            </div>

            <p class="batch-meta">
                ${batch.document_count} document(s)
                · ${escapeHTML(batch.created_at)}
            </p>

            <p class="batch-meta">
                ${files}
            </p>

            <div class="batch-actions">
                <button class="action-button"
                    data-analyze="${escapeHTML(batch.batch_id)}">
                    Analyze Documents
                </button>

                <button class="action-button"
                    data-results="${escapeHTML(batch.batch_id)}">
                    View Results
                </button>

                <button class="action-button"
                    data-approve="${escapeHTML(batch.batch_id)}">
                    Approve & Export
                </button>
            </div>
        `;

        batchList.appendChild(card);
    });
}

batchList.addEventListener("click", async event => {
    const analyzeButton = event.target.closest("[data-analyze]");
    const resultsButton = event.target.closest("[data-results]");
    const approveButton = event.target.closest("[data-approve]");

    try {
        if (analyzeButton) {
            await analyzeBatch(analyzeButton.dataset.analyze);
        }

        if (resultsButton) {
            await loadResults(resultsButton.dataset.results);
        }

        if (approveButton) {
            await approveBatch(approveButton.dataset.approve);
        }

    } catch (error) {
        alert(error.message);
    }
});

// --------------------------------------------------
// AI ANALYSIS
// --------------------------------------------------

async function analyzeBatch(batchId) {
    if (!confirm("Start AI analysis for this batch?")) {
        return;
    }

    analysisResults.innerHTML = `
        <p class="empty-state">
            AI analysis is running. Please wait...
        </p>
    `;

    const result = await apiRequest(
        `/api/analyze/${encodeURIComponent(batchId)}`,
        { method: "POST" }
    );

    renderAnalysis(result.result);

    alert("Analysis completed. Review the results carefully.");
}

// --------------------------------------------------
// DISPLAY ANALYSIS
// --------------------------------------------------

async function loadResults(batchId) {
    const result = await apiRequest(
        `/api/results/${encodeURIComponent(batchId)}`
    );

    renderAnalysis(result);
}

function renderAnalysis(result) {
    const trips = result.trips || [];
    const tolls = result.toll_reimbursements || [];
    const parking = result.parking_reimbursements || [];

    const driverName = result.driver?.name || "Unidentified";

    let html = `
        <div class="result-section">
            <h3>Driver Information</h3>
            <div class="result-item">
                <p><strong>Driver:</strong>
                    ${escapeHTML(driverName)}
                </p>
                <p><strong>Documents:</strong>
                    ${result.documents_processed || 0}
                </p>
                <p><strong>Approval:</strong>
                    ${escapeHTML(result.approval_status || "pending_review")}
                </p>
            </div>
        </div>

        <div class="result-section">
            <h3>Trip Records (${trips.length})</h3>
    `;

    trips.forEach((trip, index) => {
        html += `
            <div class="result-item">
                <p><strong>Trip ${index + 1}</strong></p>
                <p>Date: ${escapeHTML(trip.trip_date)}</p>
                <p>Trip Ticket: ${escapeHTML(trip.trip_ticket_number)}</p>
                <p>Line Haul: ${escapeHTML(trip.line_haul_trip_number)}</p>
                <p>Origin: ${escapeHTML(trip.origin)}</p>
                <p>Destination: ${escapeHTML(trip.destination)}</p>
                <p>Segments: ${(trip.segments || []).length}</p>
                <p>Review Flags:
                    ${escapeHTML((trip.review_flags || []).join("; ") || "None")}
                </p>
            </div>
        `;
    });

    html += `
        </div>
        <div class="result-section">
            <h3>Toll Reimbursements (${tolls.length})</h3>
    `;

    tolls.forEach(record => {
        html += `
            <div class="result-item">
                <p>Date: ${escapeHTML(record.date)}</p>
                <p>Location: ${escapeHTML(record.location)}</p>
                <p>Amount: ${escapeHTML(record.amount)}</p>
            </div>
        `;
    });

    html += `
        </div>
        <div class="result-section">
            <h3>Parking Reimbursements (${parking.length})</h3>
    `;

    parking.forEach(record => {
        html += `
            <div class="result-item">
                <p>Date: ${escapeHTML(record.date)}</p>
                <p>Location: ${escapeHTML(record.location)}</p>
                <p>Amount: ${escapeHTML(record.amount)}</p>
            </div>
        `;
    });

    html += `
        </div>
        <div class="result-section">
            <h3>Unresolved Items</h3>
            <div class="result-item">
                ${escapeHTML(
                    (result.unresolved_items || []).join("; ") ||
                    "No unresolved items reported."
                )}
            </div>
        </div>
    `;

    analysisResults.innerHTML = html;
}

// --------------------------------------------------
// APPROVAL AND EXPORT
// --------------------------------------------------

async function approveBatch(batchId) {
    const result = await apiRequest(
        `/api/results/${encodeURIComponent(batchId)}`
    );

    if (result.overall_review_required !== false) {
        alert(
            "This batch still requires review. " +
            "The system will not approve it automatically."
        );

        return;
    }

    if (!confirm(
        "Have you reviewed this batch and verified all records? " +
        "Approval will export the data to Google Sheets."
    )) {
        return;
    }

    result.approval_status = "approved";

    // The backend must also persist this approval state
    // before export. The current starter endpoint does not
    // yet provide that operation, so export remains blocked
    // until the approval API is implemented.
    alert(
        "Approval workflow is not yet enabled. " +
        "Backend approval persistence must be implemented first."
    );
}

// --------------------------------------------------
// INITIALIZATION
// --------------------------------------------------

async function initializeDashboard() {
    await checkSystem();
    await loadBatches();
}

initializeDashboard();
