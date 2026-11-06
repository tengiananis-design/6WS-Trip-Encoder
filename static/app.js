
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
const MAX_FILES_PER_BATCH = 200;
const MAX_FILE_SIZE_BYTES = 50 * 1024 * 1024;
const MAX_BATCH_SIZE_BYTES = 1024 * 1024 * 1024;

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

    const invalid = [];
    let added = 0;
    for (const file of incomingFiles) {
        if (!file.name.toLowerCase().endsWith('.pdf') || (file.type && file.type !== 'application/pdf')) {
            invalid.push(file.name + ': not a PDF'); continue;
        }
        if (file.size > MAX_FILE_SIZE_BYTES) { invalid.push(file.name + ': exceeds 50 MB'); continue; }
        const duplicate = selectedPDFs.some(existing => existing.name === file.name && existing.size === file.size && existing.lastModified === file.lastModified);
        if (duplicate) continue;
        if (selectedPDFs.length >= MAX_FILES_PER_BATCH) { invalid.push(file.name + ': batch limit is 200 PDFs'); continue; }
        const currentBytes = selectedPDFs.reduce((sum, item) => sum + item.size, 0);
        if (currentBytes + file.size > MAX_BATCH_SIZE_BYTES) { invalid.push(file.name + ': batch would exceed 1 GB'); continue; }
        selectedPDFs.push(file); added += 1;
    }
    if (invalid.length) {
        showMessage(uploadMessage, invalid.slice(0, 5).join(' · ') + (invalid.length > 5 ? ' · and ' + (invalid.length - 5) + ' more' : ''), 'error');
    } else if (added) {
        showMessage(uploadMessage, added + ' PDF(s) added to this batch.', 'success');
    }
    renderSelectedFiles();;
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

            </div>
        `;

        batchList.appendChild(card);
    });
}

batchList.addEventListener("click", async event => {
    const analyzeButton = event.target.closest("[data-analyze]");
    const resultsButton = event.target.closest("[data-results]");

    try {
        if (analyzeButton) {
            await analyzeBatch(analyzeButton.dataset.analyze);
        }

        if (resultsButton) {
            await loadResults(resultsButton.dataset.results);
        }

    } catch (error) {
        alert(error.message);
    }
});

// --------------------------------------------------
// AI ANALYSIS
// --------------------------------------------------

const wait = ms => new Promise(resolve => setTimeout(resolve, ms));

async function analyzeBatch(batchId) {
    if (!confirm("Queue AI analysis for this batch? You can leave the page while it runs.")) return;
    analysisResults.innerHTML = '<p class="empty-state">Queueing analysis...</p>';
    await apiRequest(`/api/analyze/${encodeURIComponent(batchId)}`, { method: "POST" });
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
        if (state.status === "failed" || state.status === "queue_failed") {
            analysisResults.innerHTML = `<div class="result-item"><h3>Analysis ${escapeHTML(state.status)}</h3><p>${escapeHTML(state.error || "The worker could not complete this batch.")}</p></div>`;
            await loadBatches();
            return;
        }
        analysisResults.innerHTML = `<p class="empty-state">Analysis ${escapeHTML(state.status || "pending")}... Checking again in 5 seconds.</p>`;
        await wait(5000);
    }
}

// --------------------------------------------------
// DISPLAY AND EDIT ANALYSIS
// --------------------------------------------------

let activeReviewBatchId = null;
let activeReviewResult = null;

const reviewSections = [
    {key:"trips", title:"Trip Records", fields:[
        ["trip_date","Trip date"],["line_haul_trip_number","Linehaul trip no."],
        ["driver_id","Driver ID"],["driver_name","Driver name"],["plate_no","Plate no."],
        ["fleet_type","Fleet type"],["check_in_time_actual","Check-in time"],
        ["valid_time_circled","Valid time (circled)"],["docking_time","Docking time"],
        ["docking_date_or_check_out_date","Docking / check-out date"],
        ["check_out_time","Check-out time"],["origin","Origin"],["drop_offs","Drop-offs"],
        ["destination","Destination"],["with_backload","With backload"],
        ["demurrage_availment","Demurrage"],["remarks","Remarks"],["late","Late"]
    ]},
    {key:"toll_reimbursements", title:"Toll Reimbursements", fields:[
        ["date","Date"],["line_haul_trip_number","Linehaul trip no."],
        ["driver_id","Driver ID"],["driver_name","Driver name"],["plate_no","Plate no."],
        ["fleet_type","Fleet type"],["origin","Origin"],["destination","Destination"],
        ["rfid_toll_charges","RFID / provider"],["entry_point","Entry point"],
        ["exit_point","Exit point"],["date_of_or","Date of OR"],["tag_number","Tag number"],["amount","Amount"]
    ]},
    {key:"parking_reimbursements", title:"Parking Reimbursements", fields:[
        ["trip_date","Trip date"],["line_haul_trip_number","Linehaul trip no."],
        ["driver_id","Driver ID"],["driver_name","Driver name"],["plate_no","Plate no."],
        ["fleet_type","Fleet type"],["origin","Origin"],["destination","Destination"],
        ["vendor_name","Vendor"],["address","Address"],["date_of_or","Date of OR"],
        ["tag_number","Tag number"],["amount","Amount"]
    ]},
    {key:"passway_reimbursements", title:"Passway Reimbursements", fields:[
        ["trip_date","Trip date"],["line_haul_trip_number","Linehaul trip no."],
        ["driver_id","Driver ID"],["driver_name","Driver name"],["plate_no","Plate no."],
        ["fleet_type","Fleet type"],["origin","Origin"],["destination","Destination"],
        ["vendor_name","Vendor"],["address","Address"],["date_of_or","Date of OR"],
        ["tag_number","Tag number"],["amount","Amount"]
    ]}
];

function renderAnalysis(result, batchId) {
    activeReviewBatchId = batchId || result.batch_id || null;
    activeReviewResult = JSON.parse(JSON.stringify(result));
    const drivers = (result.drivers || []).map(group => {
        const driver = group.driver;
        return typeof driver === "object" ? (driver.name || "Unidentified") : (driver || "Unidentified");
    });
    let html = '<div class="result-item"><h3>Analysis overview</h3>' +
        '<p><strong>Batch:</strong> ' + escapeHTML(activeReviewBatchId || "Unknown") + '</p>' +
        '<p><strong>Documents processed:</strong> ' + Number(result.documents_processed || 0) +
        ' / ' + Number(result.documents_total || 0) + '</p>' +
        '<p><strong>Drivers identified:</strong> ' + Number(result.driver_count || 0) + '</p>' +
        '<p><strong>Drivers:</strong> ' + escapeHTML(drivers.join(", ") || "Unidentified") + '</p>' +
        '<p><strong>Approval:</strong> Pending human review. Export is disabled.</p>' +
        '<p><strong>Unidentified documents:</strong> ' +
        ((result.grouping && result.grouping.unidentified_documents) || []).length + '</p>' +
        '<p><strong>Extraction errors:</strong> ' + (result.extraction_errors || []).length + '</p></div>' +
        '<p class="review-note">Edit extracted fields below, then save. Source movement evidence and review flags are retained. Saving does not approve or export this batch.</p>';

    const unidentifiedDocs = (result.grouping && result.grouping.unidentified_documents) || [];
    if (unidentifiedDocs.length) {
        const knownDrivers = (result.drivers || []).map(item =>
            item.driver && typeof item.driver === "object" ? item.driver.name : item.driver
        ).filter(Boolean);
        html += '<div class="result-section"><h3>Unassigned documents (' + unidentifiedDocs.length +
            ')</h3><p>Assign each document to the correct driver. A manual assignment overrides the extracted name but remains pending human review.</p>';
        unidentifiedDocs.forEach((doc, docIndex) => {
            const filename = doc.filename || "unknown.pdf";
            html += '<div class="result-item assignment-row"><strong>' + escapeHTML(filename) +
                '</strong><label class="review-field"><span>Driver name</span><input type="text" list="knownDriverNames" ' +
                'data-assign-filename="' + escapeHTML(filename) + '" placeholder="Enter or select driver name"></label>' +
                '<button type="button" class="secondary-button" data-assign-driver="' + docIndex +
                '">Assign document</button><span class="message" data-assignment-message="' + docIndex + '"></span></div>';
        });
        html += '<datalist id="knownDriverNames">';
        knownDrivers.forEach(name => { html += '<option value="' + escapeHTML(name) + '"></option>'; });
        html += '</datalist></div>';
    }

    reviewSections.forEach(section => {
        const rows = result[section.key] || [];
        html += '<div class="result-section"><h3>' + section.title + ' (' + rows.length + ')</h3>';
        if (!rows.length) html += '<p class="empty-state">No records found.</p>';
        rows.forEach((row,index) => {
            html += '<div class="result-item review-record"><h4>' + section.title + ' #' + (index+1) + '</h4><div class="review-grid">';
            section.fields.forEach(pair => {
                const field = pair[0], label = pair[1];
                let value = row[field];
                if (field === "drop_offs" && Array.isArray(value)) value = value.join(", ");
                html += '<label class="review-field"><span>' + escapeHTML(label) + '</span>' +
                    '<input type="text" data-review-section="' + section.key +
                    '" data-review-index="' + index + '" data-review-field="' + field +
                    '" value="' + escapeHTML(value == null ? "" : value) + '"></label>';
            });
            html += '</div>';
            if (row.segments && row.segments.length) {
                html += '<p><strong>Movement rows (edit route and times):</strong></p><div class="movement-list">';
                row.segments.forEach((segment, segmentIndex) => {
                    html += '<div class="movement-row"><strong>Movement ' + (segmentIndex + 1) +
                        ' · ' + escapeHTML(segment.movement_type || "unclassified") + '</strong><div class="review-grid">';
                    [["origin","From"],["destination","To"],["departure_time","Departure time"],["arrival_time","Arrival time"]].forEach(pair => {
                        html += '<label class="review-field"><span>' + pair[1] + '</span><input type="text" ' +
                            'data-segment-index="' + segmentIndex + '" data-review-section="trips" ' +
                            'data-review-index="' + index + '" data-review-field="segments.' + pair[0] + '" ' +
                            'value="' + escapeHTML(segment[pair[0]] || "") + '"></label>';
                    });
                    html += '</div></div>';
                });
                html += '</div>';
            }
            const sourcePages = row.source_pages || [];
            if (row.source_filename && sourcePages.length) {
                html += '<div class="source-evidence"><strong>Source evidence · ' +
                    escapeHTML(row.source_filename) + '</strong><div class="source-page-list">';
                sourcePages.forEach(pageNumber => {
                    const imageUrl = '/api/source/' + encodeURIComponent(activeReviewBatchId) + '/' +
                        encodeURIComponent(row.source_filename) + '/page/' + Number(pageNumber);
                    html += '<figure class="source-page"><figcaption>PDF page ' + Number(pageNumber) +
                        '</figcaption><a href="' + imageUrl + '" target="_blank" rel="noopener"><img loading="lazy" src="' +
                        imageUrl + '" alt="Source PDF page ' + Number(pageNumber) + '"></a></figure>';
                });
                html += '</div></div>';
            }
            html += '<p class="review-flags"><strong>Review flags:</strong> ' +
                escapeHTML((row.review_flags || []).join("; ") || "None reported") + '</p></div>';
        });
        html += '</div>';
    });

    const unresolved = result.unresolved_items || [];
    const errors = result.extraction_errors || [];
    if (unresolved.length || errors.length) {
        html += '<div class="result-section"><h3>Unresolved items and extraction errors</h3>';
        [...unresolved, ...errors].forEach(item => {
            html += '<div class="result-item">' +
                escapeHTML(typeof item === "string" ? item : JSON.stringify(item)) + '</div>';
        });
        html += '</div>';
    }
    html += '<button type="button" class="primary-button" id="saveReviewButton">Save review edits</button>' +
        '<span id="reviewSaveMessage" class="message"></span>';
    analysisResults.innerHTML = html;
    document.getElementById("saveReviewButton").addEventListener("click", saveReviewEdits);
    analysisResults.querySelectorAll("[data-assign-driver]").forEach(button => {
        button.addEventListener("click", () => assignDocumentDriver(button));
    });
}

async function assignDocumentDriver(button) {
    const index = Number(button.dataset.assignDriver);
    const message = analysisResults.querySelector('[data-assignment-message="' + index + '"]');
    const row = button.closest(".assignment-row");
    const nameInput = row?.querySelector("input[data-assign-filename]");
    if (!nameInput || !nameInput.value.trim()) {
        message.textContent = "Enter a driver name first.";
        message.className = "message error";
        return;
    }
    button.disabled = true;
    message.textContent = "Saving assignment...";
    try {
        const response = await apiRequest("/api/assign-driver/" + encodeURIComponent(activeReviewBatchId), {
            method: "POST",
            headers: {"Content-Type":"application/json"},
            body: JSON.stringify({filename: nameInput.dataset.assignFilename, driver_name: nameInput.value.trim()})
        });
        message.textContent = response.message || "Assignment saved.";
        message.className = "message success";
        await loadResults(activeReviewBatchId);
    } catch (error) {
        message.textContent = error.message;
        message.className = "message error";
    } finally {
        button.disabled = false;
    }
}

async function saveReviewEdits() {
    if (!activeReviewBatchId || !activeReviewResult) return;
    const button = document.getElementById("saveReviewButton");
    const message = document.getElementById("reviewSaveMessage");
    button.disabled = true;
    message.textContent = "Saving edits...";
    try {
        const updated = JSON.parse(JSON.stringify(activeReviewResult));
        document.querySelectorAll("[data-review-section]").forEach(input => {
            const section = input.dataset.reviewSection;
            const index = Number(input.dataset.reviewIndex);
            const field = input.dataset.reviewField;
            const value = input.value.trim();
            if (field.startsWith("segments.")) {
                const segmentField = field.split(".")[1];
                const segmentIndex = Number(input.dataset.segmentIndex);
                updated.trips[index].segments[segmentIndex][segmentField] = value || null;
            } else {
                updated[section][index][field] = section === "trips" && field === "drop_offs"
                    ? (value ? value.split(",").map(part => part.trim()).filter(Boolean) : [])
                    : (value || null);
            }
        });
        const response = await apiRequest("/api/review/" + encodeURIComponent(activeReviewBatchId), {
            method: "PUT",
            headers: {"Content-Type":"application/json"},
            body: JSON.stringify(updated)
        });
        activeReviewResult = updated;
        message.textContent = response.message || "Review edits saved.";
        message.className = "message success";
        await loadBatches();
    } catch (error) {
        message.textContent = error.message;
        message.className = "message error";
    } finally {
        button.disabled = false;
    }
}

async function loadResults(batchId) {
    const state = await apiRequest("/api/results/" + encodeURIComponent(batchId));
    if (state.status === "completed" && state.result) {
        renderAnalysis(state.result, batchId);
    } else if (["failed","queue_failed"].includes(state.status)) {
        analysisResults.innerHTML = '<div class="result-item"><h3>Analysis ' +
            escapeHTML(state.status) + '</h3><p>' +
            escapeHTML(state.error || "The worker could not complete this batch.") + '</p></div>';
    } else {
        analysisResults.innerHTML = '<p class="empty-state">Batch status: ' +
            escapeHTML(state.status || "pending") + '.</p>';
    }
}

// INITIALIZATION
// --------------------------------------------------

async function initializeDashboard() {
    await checkSystem();
    await loadBatches();
}

initializeDashboard();
