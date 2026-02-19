/**
 * MedBill AI — Dashboard Application v2
 * ========================================
 * Now connected to the FastAPI backend for real bill processing.
 * Supports:
 *   - Real file upload with progress tracking via SSE
 *   - Live API calls for bill processing
 *   - CGHS rate and hospital search
 *   - Bill history management
 *   - Falls back to sample data when no processed bills exist
 */

const API_BASE = '';  // Same origin

// ============================================
// Sample Data (fallback when no real bills)
// ============================================

const SAMPLE_BILL = {
    id: "sample_001",
    source_file: "Sample Bill (1).pdf",
    total_pages: 125,
    extraction_timestamp: "2026-02-18T12:34:43",
    patient: {
        name: "Mr. Anil Kumar Pandey",
        age: "62",
        gender: "Male",
        uhid: "BMC0049654",
        ip_number: "IP-25-03-1947",
        employee_id: "90262908",
        relationship: "SELF"
    },
    hospital: {
        name: "Balco Medical Centre",
        address: "Sector 36, Atal Nagar (Naya Raipur)",
        city: "Naya Raipur, Chhattisgarh",
        phone: "0771-2237575",
        registration_number: "CG-RZ1-45774"
    },
    admission: {
        admission_date: "19/03/2025",
        admission_time: "12:44 PM",
        discharge_date: "28/04/2025",
        discharge_time: "",
        days_stayed: 40,
        ward_type: "CGHS Ward",
        diagnosis: "Adenocarcinoma of GEJ (Gastroesophageal Junction) — Siewert Type II, post 4 cycles FLOT chemotherapy",
        procedures: [
            "Laparoscopy Diagnostic (CN140033 CGHS Grade-I)",
            "Total Esophagectomy Trans Thoracic (CN140392 CGHS Grade-VI)",
            "Esophagus R.A. (CN140340 CGHS Grade-IV)",
            "Jejunostomy/Gastrostomy (CN140096 CGHS Grade-II)",
            "ICD Placement (CN140039 CGHS Grade-I)"
        ],
        referring_doctor: "Ref: SECL/BSP/MED/PRBC/2025/177",
        treating_doctor: "Dr. Shravan Nadkarni (Surgical Oncology)"
    },
    line_items: [
        { item_type: "CONSULTATION", description: "OPD Consultation — Dr. Santosh Tharwani", quantity: 1, amount: 350 },
        { item_type: "CONSULTATION", description: "OPD Consultation — Dr. Shravan Nadkarni", quantity: 1, amount: 350 },
        { item_type: "PROCEDURE", description: "Total Esophagectomy (Trans Thoracic) + Jejunostomy + ICD", quantity: 1, amount: 650000 },
        { item_type: "ROOM_RENT", description: "Ward Charges (40 days)", quantity: 40, amount: 80000 },
        { item_type: "ICU", description: "ICU / HDU Charges (estimated)", quantity: 10, amount: 75000 },
        { item_type: "MEDICINE", description: "Pharmacy & Drug Charges", quantity: 1, amount: 45000 },
        { item_type: "INVESTIGATION", description: "Lab, Radiology & Imaging", quantity: 1, amount: 28000 },
        { item_type: "CONSUMABLE", description: "Surgical Consumables & Disposables", quantity: 1, amount: 18000 },
        { item_type: "OTHER", description: "Nursing, OT, Anesthesia & Misc.", quantity: 1, amount: 5679 }
    ],
    total_amount: 902379,
    discount: 0,
    net_amount: 902379,
    advance_paid: 0,
    balance_due: 0,
    bill_number: "INV-BMC-25004271",
    bill_date: "28/04/2025",
    ocr_confidence: 73.19,
    extraction_method: "OCR_LLM"
};

const SAMPLE_VALIDATION = [
    { rule_id: "E001", status: "pass", severity: "INFO", message: "Employee 90262908 registered under CPRMSE scheme" },
    { rule_id: "E002", status: "pass", severity: "INFO", message: "Patient Mr. Anil Kumar Pandey is the primary beneficiary (SELF)" },
    { rule_id: "E003", status: "fail", severity: "ERROR", message: "Hospital 'Balco Medical Centre' not found in empanelled list — verify NABH status & CIL empanelment" },
    { rule_id: "E004", status: "pass", severity: "INFO", message: "Referral SECL/BSP/MED/PRBC/2025/177 dated 13/03/2025 — admission 6 days after referral (within 45-day limit)" },
    { rule_id: "R001", status: "pass", severity: "INFO", message: "Consultation OPD ₹350 within CGHS rate ₹350" },
    { rule_id: "R002", status: "warn", severity: "WARNING", message: "No CGHS rate found for 'Total Esophagectomy (Trans Thoracic)' — requires manual verification against AIIMS rates" },
    { rule_id: "RR001", status: "pass", severity: "INFO", message: "Grade E-2 (CPRMSE retired) entitled to Twin Sharing (AC) room" },
    { rule_id: "P001", status: "fail", severity: "ERROR", message: "Package procedure claimed but separate consultation ₹350 also charged — potential double billing" },
    { rule_id: "HV001", status: "warn", severity: "WARNING", message: "Bill ₹9,02,379 exceeds ₹1 lakh — requires scrutiny by 2 medical officers" },
    { rule_id: "HV002", status: "fail", severity: "ERROR", message: "Stay of 40 days exceeds 15 days — CMS approval documentation required but not verified" },
    { rule_id: "HV003", status: "warn", severity: "WARNING", message: "Bill ₹9,02,379 exceeds ₹5 lakhs — requires Area GM endorsement" },
    { rule_id: "D001", status: "pass", severity: "INFO", message: "Discharge summary page detected in document" },
    { rule_id: "F001", status: "pass", severity: "INFO", message: "No duplicate bill submission detected for INV-BMC-25004271" }
];

const VALIDATION_RULES_DATA = [
    {
        category: "Eligibility",
        rules: [
            { id: "E001", desc: "Employee must be registered under CPRMSE/CPRMSNE scheme" },
            { id: "E002", desc: "Patient must be listed as self, spouse, or eligible dependent" },
            { id: "E003", desc: "Hospital must be empanelled under CIL for the treatment type" },
            { id: "E004", desc: "Referral letter must be obtained within 45 days before admission" }
        ]
    },
    {
        category: "Rate Validation",
        rules: [
            { id: "R001", desc: "Each line item must not exceed CGHS rate (NABH if applicable)" },
            { id: "R002", desc: "If CGHS rate not found, apply AIIMS rate as ceiling" },
            { id: "R003", desc: "Pharmacy items outside formulary restricted to MRP - 14% trade margin" }
        ]
    },
    {
        category: "Room Rent",
        rules: [
            { id: "RR001", desc: "Room category must match employee grade entitlement" },
            { id: "RR002", desc: "Room rent per day must not exceed CGHS ceiling for the entitled category" },
            { id: "RR003", desc: "If higher room used, proportionate deduction applies to all charges" }
        ]
    },
    {
        category: "Package Rules",
        rules: [
            { id: "P001", desc: "If procedure has CGHS package, no separate charges for included items" },
            { id: "P002", desc: "Package rate is the ceiling — actual charges may be lower" },
            { id: "P003", desc: "Implants/prostheses billed separately only if not in package definition" }
        ]
    },
    {
        category: "Consultation",
        rules: [
            { id: "C001", desc: "Max one consultation fee per specialist per day" },
            { id: "C002", desc: "Consultation fee must not exceed CGHS rate" },
            { id: "C003", desc: "Cross-specialty referral to be accompanied by documented rationale" }
        ]
    },
    {
        category: "Documentation",
        rules: [
            { id: "D001", desc: "Discharge summary must be attached for all IPD claims" },
            { id: "D002", desc: "Original bills with hospital stamp required" },
            { id: "D003", desc: "Prescription copies mandatory for pharmacy claims" },
            { id: "D004", desc: "Pre-authorization for planned admissions above the threshold" }
        ]
    },
    {
        category: "High-Value Bills",
        rules: [
            { id: "HV001", desc: "Bills > ₹1 lakh require scrutiny by 2 medical officers" },
            { id: "HV002", desc: "Stay exceeding 15 days requires CMS/CMO approval documentation" },
            { id: "HV003", desc: "Bills > ₹5 lakhs require area GM endorsement" }
        ]
    },
    {
        category: "Fraud Detection",
        rules: [
            { id: "F001", desc: "Check for duplicate bill submission (same bill number + hospital)" },
            { id: "F002", desc: "Flag if bill amount is > 3σ from historical mean for similar procedures" },
            { id: "F003", desc: "Cross-reference admission overlap across claims" }
        ]
    },
    {
        category: "OPD & Domiciliary",
        rules: [
            { id: "OPD001", desc: "OPD reimbursement limited to annual ceiling per grade" },
            { id: "OPD002", desc: "Domiciliary treatment needs treating doctor certification" }
        ]
    },
    {
        category: "Spectacles",
        rules: [
            { id: "SP001", desc: "Spectacles reimbursement once every 2 years" },
            { id: "SP002", desc: "Amount limited to grade-wise ceiling" }
        ]
    }
];


// ============================================
// Application State
// ============================================

const state = {
    currentTab: 'dashboard',
    bills: [],
    selectedBill: null,
    validationResults: [],
    currentJob: null,
    aiStatus: 'unknown',
};


// ============================================
// DOM Utilities
// ============================================

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

function formatCurrency(amount) {
    if (amount === 0 || amount === undefined || amount === null) return '₹0';
    return '₹' + amount.toLocaleString('en-IN', { maximumFractionDigits: 0 });
}

function formatCurrencyFull(amount) {
    if (amount === 0 || amount === undefined || amount === null) return '₹0.00';
    return '₹' + amount.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}


// ============================================
// API Helpers
// ============================================

async function apiGet(endpoint) {
    try {
        const resp = await fetch(`${API_BASE}${endpoint}`);
        if (!resp.ok) throw new Error(`API error: ${resp.status}`);
        return await resp.json();
    } catch (e) {
        console.error(`API GET ${endpoint} failed:`, e);
        return null;
    }
}

async function apiPost(endpoint, formData) {
    try {
        const resp = await fetch(`${API_BASE}${endpoint}`, {
            method: 'POST',
            body: formData,
        });
        if (!resp.ok) {
            const err = await resp.json().catch(() => ({ detail: resp.statusText }));
            throw new Error(err.detail || resp.statusText);
        }
        return await resp.json();
    } catch (e) {
        console.error(`API POST ${endpoint} failed:`, e);
        throw e;
    }
}


// ============================================
// Tab Navigation
// ============================================

function initNavigation() {
    $$('.nav-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            const tab = btn.dataset.tab;
            switchTab(tab);
        });
    });
}

function switchTab(tab) {
    state.currentTab = tab;

    $$('.nav-btn').forEach(b => b.classList.remove('active'));
    $(`.nav-btn[data-tab="${tab}"]`).classList.add('active');

    $$('.tab-content').forEach(t => t.classList.remove('active'));
    $(`#tab-${tab}`).classList.add('active');
}


// ============================================
// Data Loading
// ============================================

async function loadBills() {
    const data = await apiGet('/api/bills');
    if (data && data.bills && data.bills.length > 0) {
        state.bills = data.bills;
        // Load the most recent bill's full details
        const latestBill = data.bills[data.bills.length - 1];
        const fullBill = await apiGet(`/api/bills/${latestBill.id}`);
        if (fullBill) {
            state.selectedBill = fullBill;
            state.validationResults = fullBill.validation_results || [];
        }
    }

    // Fall back to sample data if no real bills
    if (!state.selectedBill) {
        state.selectedBill = SAMPLE_BILL;
        state.validationResults = SAMPLE_VALIDATION;
        state.bills = [SAMPLE_BILL];
    }
}


// ============================================
// Dashboard Rendering
// ============================================

function renderDashboard() {
    renderStats();
    renderBillSelector();
    renderBillDetail();
    renderLineItems();
    renderValidation();
    renderMetadata();
}

function renderStats() {
    const bill = state.selectedBill;
    const results = state.validationResults;

    const passed = results.filter(r => (r.status === 'pass')).length;
    const failed = results.filter(r => (r.status === 'fail')).length;
    const warnings = results.filter(r => (r.status === 'warn')).length;

    animateNumber('total-bills-count', 0, state.bills.length, 500);
    animateNumber('passed-count', 0, passed, 700);
    animateNumber('flagged-count', 0, failed + warnings, 900);

    setTimeout(() => {
        $('#total-amount').textContent = formatCurrency(bill.total_amount);
    }, 400);
}

function renderBillSelector() {
    // If there's a bill selector container, populate it
    const selectorContainer = $('#bill-selector');
    if (!selectorContainer || state.bills.length <= 1) return;

    selectorContainer.innerHTML = state.bills.map((b, i) => {
        const isSelected = b.id === state.selectedBill?.id;
        return `
            <button class="bill-selector-btn ${isSelected ? 'active' : ''}" 
                    onclick="selectBill('${b.id}')" 
                    id="bill-btn-${i}">
                <span class="bill-sel-name">${b.patient_name || b.source_file || 'Bill'}</span>
                <span class="bill-sel-amount">${formatCurrency(b.total_amount)}</span>
            </button>
        `;
    }).join('');
}

async function selectBill(billId) {
    const fullBill = await apiGet(`/api/bills/${billId}`);
    if (fullBill) {
        state.selectedBill = fullBill;
        state.validationResults = fullBill.validation_results || [];
        renderDashboard();
    }
}

function animateNumber(elementId, start, end, duration) {
    const el = document.getElementById(elementId);
    if (!el) return;

    const startTime = performance.now();

    function update(currentTime) {
        const elapsed = currentTime - startTime;
        const progress = Math.min(elapsed / duration, 1);
        const eased = 1 - Math.pow(1 - progress, 3);

        el.textContent = Math.round(start + (end - start) * eased);

        if (progress < 1) {
            requestAnimationFrame(update);
        }
    }

    requestAnimationFrame(update);
}

function renderBillDetail() {
    const bill = state.selectedBill;
    const results = state.validationResults;
    const failed = results.filter(r => r.status === 'fail').length;

    // Status badge
    const badge = $('#detail-status-badge');
    badge.className = 'detail-badge';
    if (failed > 0) {
        badge.classList.add('failed');
        badge.querySelector('.badge-text').textContent = `${failed} Issue${failed > 1 ? 's' : ''} Found`;
    } else {
        badge.classList.add('passed');
        badge.querySelector('.badge-text').textContent = 'Approved';
    }

    // Patient
    const patient = bill.patient || {};
    $('#p-name').textContent = patient.name || '—';
    $('#p-age-gender').textContent = [patient.age ? `${patient.age} yrs` : '', patient.gender].filter(Boolean).join(' / ') || '—';
    $('#p-emp-id').textContent = patient.employee_id || '—';
    $('#p-uhid').textContent = patient.uhid || '—';
    $('#p-relation').textContent = patient.relationship || '—';

    // Hospital
    const hospital = bill.hospital || {};
    $('#h-name').textContent = hospital.name || '—';
    $('#h-city').textContent = hospital.city || '—';

    const empResult = results.find(r => (r.rule_id || r.id) === 'E003');
    if (empResult) {
        const empEl = $('#h-empanelled');
        if (empResult.status === 'pass') {
            empEl.textContent = '✅ Empanelled';
            empEl.style.color = 'var(--success)';
        } else if (empResult.status === 'fail') {
            empEl.textContent = '❌ Not Found';
            empEl.style.color = 'var(--danger)';
        } else {
            empEl.textContent = '⚠️ Not Verified';
            empEl.style.color = 'var(--warning)';
        }
    }
    $('#h-nabh').textContent = hospital.nabh_status || 'Unknown';

    // Admission
    const admission = bill.admission || {};
    $('#a-admitted').textContent = admission.admission_date || '—';
    $('#a-discharged').textContent = admission.discharge_date || '—';
    $('#a-stay').textContent = admission.days_stayed ? `${admission.days_stayed} days` : '—';
    $('#a-diagnosis').textContent = admission.diagnosis || '—';
    $('#a-doctor').textContent = admission.treating_doctor || '—';

    // Financial
    $('#f-bill-no').textContent = bill.bill_number || '—';
    $('#f-bill-date').textContent = bill.bill_date || '—';
    $('#f-total').textContent = formatCurrency(bill.total_amount);
    $('#f-discount').textContent = formatCurrency(bill.discount);
    $('#f-net').textContent = formatCurrency(bill.net_amount);
    $('#f-advance').textContent = formatCurrency(bill.advance_paid);
    $('#f-balance').textContent = formatCurrency(bill.balance_due);
}

function renderLineItems() {
    const items = state.selectedBill.line_items || [];
    const tbody = $('#line-items-body');
    const count = $('#item-count');

    count.textContent = `${items.length} items`;

    tbody.innerHTML = items.map((item, i) => {
        const typeLower = (item.item_type || 'OTHER').toLowerCase().replace(/ /g, '_');
        return `
            <tr style="animation: slideUp 0.3s ease ${0.05 * i}s backwards">
                <td><span class="type-badge ${typeLower}">${(item.item_type || 'OTHER').replace(/_/g, ' ')}</span></td>
                <td>${item.description || '—'}</td>
                <td>${item.quantity || 1}</td>
                <td class="align-right" style="font-family: var(--font-mono); font-weight: 600;">${formatCurrencyFull(item.amount)}</td>
            </tr>
        `;
    }).join('');

    // Add total row
    const total = items.reduce((sum, i) => sum + (i.amount || 0), 0);
    tbody.innerHTML += `
        <tr style="border-top: 2px solid var(--border); font-weight: 700;">
            <td colspan="3" style="text-align: right; padding-right: 20px;">TOTAL</td>
            <td class="align-right" style="font-family: var(--font-mono); color: var(--accent-tertiary); font-size: 1rem;">${formatCurrencyFull(total)}</td>
        </tr>
    `;
}

function renderValidation() {
    const results = state.validationResults;
    const grid = $('#validation-grid');

    const passed = results.filter(r => r.status === 'pass').length;
    const failed = results.filter(r => r.status === 'fail').length;
    const warnings = results.filter(r => r.status === 'warn').length;

    $('.v-passed').textContent = `${passed} Passed`;
    $('.v-failed').textContent = `${failed} Failed`;
    $('.v-warnings').textContent = `${warnings} Warnings`;

    // Sort: fails first, then warnings, then passes
    const sorted = [...results].sort((a, b) => {
        const order = { fail: 0, warn: 1, pass: 2 };
        return order[a.status] - order[b.status];
    });

    grid.innerHTML = sorted.map((r, i) => {
        const icon = r.status === 'pass' ? '✅' : r.status === 'fail' ? '❌' : '⚠️';
        const cls = r.status === 'pass' ? 'pass' : r.status === 'fail' ? 'fail' : 'warn';
        const ruleId = r.rule_id || r.id || '';
        return `
            <div class="validation-item ${cls}" style="animation-delay: ${0.05 * i}s">
                <span class="v-icon">${icon}</span>
                <div class="v-content">
                    <span class="v-rule-id">[${ruleId}]</span>
                    <p class="v-message">${r.message}</p>
                </div>
            </div>
        `;
    }).join('');
}

function renderMetadata() {
    const bill = state.selectedBill;

    const method = bill.extraction_method;
    $('#m-method').textContent = method === 'OCR_LLM' ? 'OCR + AI (Phi-3)' : 'OCR Only (Rule-Based)';
    $('#m-confidence').textContent = `${(bill.ocr_confidence || 0).toFixed(1)}%`;
    $('#m-pages').textContent = `${bill.total_pages || 0} pages`;
    $('#m-model').textContent = method === 'OCR_LLM' ? 'phi3:3.8b (Q4_0)' : 'Regex Rules (v1)';
    $('#m-timestamp').textContent = bill.extraction_timestamp?.replace('T', ' ').slice(0, 19) || '—';
    $('#m-source').textContent = (bill.source_file || '').split('/').pop() || '—';
}


// ============================================
// Rules Tab
// ============================================

function renderRules() {
    const grid = $('#rules-grid');

    grid.innerHTML = VALIDATION_RULES_DATA.map((cat, ci) => `
        <div class="rule-category ${ci === 0 ? 'expanded' : ''}" id="rule-cat-${ci}">
            <div class="rule-category-header" onclick="toggleRuleCategory(${ci})">
                <h4>${cat.category}</h4>
                <span class="rule-count">${cat.rules.length} rules</span>
            </div>
            <div class="rule-category-body">
                ${cat.rules.map(r => `
                    <div class="rule-item">
                        <span class="rule-id">${r.id}</span>
                        <span class="rule-desc">${r.desc}</span>
                    </div>
                `).join('')}
            </div>
        </div>
    `).join('');
}

function toggleRuleCategory(index) {
    const el = document.getElementById(`rule-cat-${index}`);
    el.classList.toggle('expanded');
}


// ============================================
// Upload Tab — Real Upload
// ============================================

function initUpload() {
    const zone = $('#upload-zone');
    const fileInput = $('#file-input');
    const browseBtn = $('#browse-btn');

    browseBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        fileInput.click();
    });

    zone.addEventListener('click', () => fileInput.click());

    zone.addEventListener('dragover', (e) => {
        e.preventDefault();
        zone.classList.add('drag-over');
    });

    zone.addEventListener('dragleave', () => {
        zone.classList.remove('drag-over');
    });

    zone.addEventListener('drop', (e) => {
        e.preventDefault();
        zone.classList.remove('drag-over');
        const file = e.dataTransfer.files[0];
        if (file && file.type === 'application/pdf') {
            startRealUpload(file);
        } else {
            showToast('Please upload a PDF file', 'warn');
        }
    });

    fileInput.addEventListener('change', () => {
        if (fileInput.files.length > 0) {
            startRealUpload(fileInput.files[0]);
        }
    });
}

async function startRealUpload(file) {
    const pipeline = $('#pipeline-container');
    pipeline.classList.remove('hidden');

    // Get processing options
    const useLLM = $('#opt-llm')?.checked ?? true;
    const dpi = parseInt($('#opt-dpi')?.value || '200');
    const maxPages = parseInt($('#opt-pages')?.value || '20');

    // Reset all pipeline steps
    ['step-repair', 'step-ocr', 'step-llm', 'step-validate'].forEach(id => {
        const el = document.getElementById(id);
        el.classList.remove('active', 'done', 'failed');
        el.querySelector('.step-status').textContent = 'Waiting...';
        el.querySelector('.progress-bar').style.width = '0%';
    });

    // Show file info
    const sizeMB = (file.size / (1024 * 1024)).toFixed(1);
    showToast(`Uploading ${file.name} (${sizeMB} MB)...`, 'info');

    try {
        // Upload the file
        const formData = new FormData();
        formData.append('file', file);

        const uploadResp = await apiPost(
            `/api/upload?use_llm=${useLLM}&dpi=${dpi}&max_pages=${maxPages}`,
            formData
        );

        if (!uploadResp || !uploadResp.job_id) {
            throw new Error('Upload failed — no job ID returned');
        }

        state.currentJob = uploadResp.job_id;
        showToast(`Processing started (Job: ${uploadResp.job_id})`, 'info');

        // Connect to SSE stream for real-time updates
        connectToJobStream(uploadResp.job_id);

    } catch (e) {
        showToast(`Upload failed: ${e.message}`, 'error');
        console.error('Upload error:', e);

        // Mark first step as failed
        const step = document.getElementById('step-repair');
        step.classList.add('failed');
        step.querySelector('.step-status').textContent = `✗ ${e.message}`;
    }
}

function connectToJobStream(jobId) {
    const eventSource = new EventSource(`${API_BASE}/api/jobs/${jobId}/stream`);

    eventSource.onmessage = (event) => {
        try {
            const data = JSON.parse(event.data);
            updatePipelineUI(data);

            if (data.status === 'completed') {
                eventSource.close();
                showToast('✅ Bill processed successfully!', 'success');

                // Reload bills and switch to dashboard
                setTimeout(async () => {
                    await loadBills();

                    // If we got a result, use it directly
                    if (data.result) {
                        state.selectedBill = data.result;
                        state.validationResults = data.result.validation_results || [];
                        if (!state.bills.find(b => b.id === data.result.id)) {
                            state.bills.push(data.result);
                        }
                    }

                    switchTab('dashboard');
                    renderDashboard();
                }, 1500);
            }

            if (data.status === 'failed') {
                eventSource.close();
                showToast(`❌ Processing failed: ${data.error}`, 'error');
            }

        } catch (e) {
            console.error('SSE parse error:', e);
        }
    };

    eventSource.onerror = () => {
        // SSE connection failed — fall back to polling
        eventSource.close();
        pollJobStatus(jobId);
    };
}

async function pollJobStatus(jobId) {
    const pollInterval = setInterval(async () => {
        const data = await apiGet(`/api/jobs/${jobId}`);
        if (!data) return;

        updatePipelineUI(data);

        if (data.status === 'completed' || data.status === 'failed') {
            clearInterval(pollInterval);

            if (data.status === 'completed') {
                showToast('✅ Bill processed successfully!', 'success');
                await loadBills();
                if (data.result) {
                    state.selectedBill = data.result;
                    state.validationResults = data.result.validation_results || [];
                }
                setTimeout(() => {
                    switchTab('dashboard');
                    renderDashboard();
                }, 1500);
            } else {
                showToast(`❌ Processing failed: ${data.error}`, 'error');
            }
        }
    }, 1000);
}

function updatePipelineUI(data) {
    const stepMap = {
        'pdf_repair': 'step-repair',
        'ocr': 'step-ocr',
        'llm': 'step-llm',
        'validate': 'step-validate',
    };

    const steps = data.steps || [];

    steps.forEach(step => {
        const elementId = stepMap[step.id];
        if (!elementId) return;

        const el = document.getElementById(elementId);
        if (!el) return;

        // Remove previous state classes
        el.classList.remove('active', 'done', 'failed');

        const statusEl = el.querySelector('.step-status');
        const barEl = el.querySelector('.progress-bar');

        switch (step.status) {
            case 'active':
                el.classList.add('active');
                statusEl.textContent = step.message || 'Processing...';
                barEl.style.width = '60%';
                break;
            case 'done':
                el.classList.add('done');
                statusEl.textContent = `✓ ${step.message || 'Done'}`;
                barEl.style.width = '100%';
                break;
            case 'failed':
                el.classList.add('failed');
                statusEl.textContent = `✗ ${step.message || 'Failed'}`;
                barEl.style.width = '100%';
                barEl.style.background = 'var(--danger)';
                break;
        }
    });
}


// ============================================
// Toast Notifications
// ============================================

function showToast(message, type = 'info') {
    // Remove existing toasts
    const existing = document.querySelector('.toast');
    if (existing) existing.remove();

    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    toast.innerHTML = `
        <span class="toast-message">${message}</span>
        <button class="toast-close" onclick="this.parentElement.remove()">×</button>
    `;
    document.body.appendChild(toast);

    // Animate in
    requestAnimationFrame(() => toast.classList.add('show'));

    // Auto remove after 5s
    setTimeout(() => {
        toast.classList.remove('show');
        setTimeout(() => toast.remove(), 300);
    }, 5000);
}


// ============================================
// AI Status Check
// ============================================

async function checkAIStatus() {
    const indicator = $('#ai-status');
    const dot = indicator.querySelector('.status-dot');
    const text = indicator.querySelector('.status-text');

    const data = await apiGet('/api/status');

    if (data && data.ai) {
        const ai = data.ai;
        if (ai.status === 'online') {
            dot.style.background = 'var(--success)';
            text.textContent = `AI Ready (${ai.models[0] || 'model'})`;
            indicator.style.background = 'var(--success-bg)';
            indicator.style.borderColor = 'var(--success-border)';
            indicator.style.color = 'var(--success)';
            state.aiStatus = 'online';
        } else if (ai.status === 'no_models') {
            dot.style.background = 'var(--warning)';
            text.textContent = 'AI: No models';
            indicator.style.background = 'var(--warning-bg)';
            indicator.style.borderColor = 'var(--warning-border)';
            indicator.style.color = 'var(--warning)';
            state.aiStatus = 'no_models';
        } else {
            dot.style.background = 'var(--danger)';
            text.textContent = 'AI Offline';
            indicator.style.background = 'var(--danger-bg)';
            indicator.style.borderColor = 'var(--danger-border)';
            indicator.style.color = 'var(--danger)';
            state.aiStatus = 'offline';
        }
    } else {
        // Server might not be up yet — try direct Ollama check
        try {
            const resp = await fetch('http://localhost:11434/api/tags');
            if (resp.ok) {
                const d = await resp.json();
                const models = d.models || [];
                if (models.length > 0) {
                    dot.style.background = 'var(--success)';
                    text.textContent = `AI Ready (${models[0].name})`;
                    indicator.style.background = 'var(--success-bg)';
                    indicator.style.borderColor = 'var(--success-border)';
                    indicator.style.color = 'var(--success)';
                    state.aiStatus = 'online';
                    return;
                }
            }
        } catch (e) { /* ignore */ }

        dot.style.background = 'var(--danger)';
        text.textContent = 'AI Offline';
        indicator.style.background = 'var(--danger-bg)';
        indicator.style.borderColor = 'var(--danger-border)';
        indicator.style.color = 'var(--danger)';
        state.aiStatus = 'offline';
    }
}


// ============================================
// CGHS Rate Search (new feature)
// ============================================

async function searchCGHS(query) {
    if (!query || query.length < 2) return [];
    const data = await apiGet(`/api/cghs/search?q=${encodeURIComponent(query)}`);
    return data?.results || [];
}

async function searchHospitals(query) {
    if (!query || query.length < 2) return [];
    const data = await apiGet(`/api/hospitals/search?q=${encodeURIComponent(query)}`);
    return data?.results || [];
}


// ============================================
// Initialize
// ============================================

document.addEventListener('DOMContentLoaded', async () => {
    initNavigation();
    initUpload();

    // Load real data from API
    await loadBills();

    // Render everything
    renderDashboard();
    renderRules();
    checkAIStatus();

    // Periodically check AI status
    setInterval(checkAIStatus, 30000);
});
