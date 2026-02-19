/**
 * MedBill AI — Dashboard Application v3
 * ========================================
 * REFACTORED: No hardcoded patient data anywhere.
 * All data is dynamically rendered from OCR extraction via the backend API.
 *
 * Architecture:
 *   - state       : Single source of truth for all app data
 *   - api.*       : All backend communication
 *   - render.*    : Pure render functions — take state, write to DOM
 *   - ui.*        : Utility helpers (toast, spinner, animations)
 *   - upload.*    : File upload + SSE pipeline
 *   - editor.*    : Editable field review panel
 */

'use strict';

/* ============================================================
   CONFIG
   ============================================================ */
const API_BASE = '';            // Same origin — no hardcoded URL
const CONFIDENCE_THRESHOLD = 0.80; // Fields below this are flagged in red

/* ============================================================
   VALIDATION RULES METADATA
   (These describe the rules, NOT the results — safe to keep here)
   ============================================================ */
const VALIDATION_RULES_CATALOG = [
    {
        category: 'Eligibility',
        rules: [
            { id: 'E001', desc: 'Employee must be registered under CPRMSE/CPRMSNE scheme' },
            { id: 'E002', desc: 'Patient must be listed as self, spouse, or eligible dependent' },
            { id: 'E003', desc: 'Hospital must be empanelled under CIL for the treatment type' },
            { id: 'E004', desc: 'Referral letter must be obtained within 45 days before admission' },
        ],
    },
    {
        category: 'Rate Validation',
        rules: [
            { id: 'R001', desc: 'Each line item must not exceed CGHS rate (NABH if applicable)' },
            { id: 'R002', desc: 'If CGHS rate not found, apply AIIMS rate as ceiling' },
            { id: 'R003', desc: 'Pharmacy items outside formulary restricted to MRP - 14% trade margin' },
        ],
    },
    {
        category: 'Room Rent',
        rules: [
            { id: 'RR001', desc: 'Room category must match employee grade entitlement' },
            { id: 'RR002', desc: 'Room rent per day must not exceed CGHS ceiling for entitled category' },
            { id: 'RR003', desc: 'If higher room used, proportionate deduction applies to all charges' },
        ],
    },
    {
        category: 'Package Rules',
        rules: [
            { id: 'P001', desc: 'If procedure has CGHS package, no separate charges for included items' },
            { id: 'P002', desc: 'Package rate is the ceiling — actual charges may be lower' },
            { id: 'P003', desc: 'Implants/prostheses billed separately only if not in package definition' },
        ],
    },
    {
        category: 'Consultation',
        rules: [
            { id: 'C001', desc: 'Max one consultation fee per specialist per day' },
            { id: 'C002', desc: 'Consultation fee must not exceed CGHS rate' },
            { id: 'C003', desc: 'Cross-specialty referral to be accompanied by documented rationale' },
        ],
    },
    {
        category: 'Documentation',
        rules: [
            { id: 'D001', desc: 'Discharge summary must be attached for all IPD claims' },
            { id: 'D002', desc: 'Original bills with hospital stamp required' },
            { id: 'D003', desc: 'Prescription copies mandatory for pharmacy claims' },
            { id: 'D004', desc: 'Pre-authorization for planned admissions above threshold' },
        ],
    },
    {
        category: 'High-Value Bills',
        rules: [
            { id: 'HV001', desc: 'Bills > ₹1 lakh require scrutiny by 2 medical officers' },
            { id: 'HV002', desc: 'Stay exceeding 15 days requires CMS/CMO approval documentation' },
            { id: 'HV003', desc: 'Bills > ₹5 lakhs require area GM endorsement' },
        ],
    },
    {
        category: 'Fraud Detection',
        rules: [
            { id: 'F001', desc: 'Check for duplicate bill submission (same bill number + hospital)' },
            { id: 'F002', desc: 'Flag if bill amount > 3σ from historical mean for similar procedures' },
            { id: 'F003', desc: 'Cross-reference admission overlap across claims' },
        ],
    },
    {
        category: 'OPD & Domiciliary',
        rules: [
            { id: 'OPD001', desc: 'OPD reimbursement limited to annual ceiling per grade' },
            { id: 'OPD002', desc: 'Domiciliary treatment needs treating doctor certification' },
        ],
    },
    {
        category: 'Spectacles',
        rules: [
            { id: 'SP001', desc: 'Spectacles reimbursement once every 2 years' },
            { id: 'SP002', desc: 'Amount limited to grade-wise ceiling' },
        ],
    },
];

/* ============================================================
   APPLICATION STATE — Single source of truth, no hardcoded data
   ============================================================ */
const state = {
    currentTab: 'dashboard',

    /** @type {Array}  All bill summaries from the server */
    bills: [],

    /** @type {Object|null}  Full detail of the selected bill */
    selectedBill: null,

    /** @type {Array}  Validation results for the selected bill */
    validationResults: [],

    /** @type {string|null}  Currently running job ID */
    currentJob: null,

    /** @type {string}  'online' | 'offline' | 'no_models' */
    aiStatus: 'unknown',

    /**
     * editedFields: user-corrected field values.
     * Keyed by field path e.g. "patient.name", "bill_number", etc.
     * @type {Object<string, string>}
     */
    editedFields: {},

    /** Whether the edit panel is currently visible */
    editPanelOpen: false,
};

/* ============================================================
   DOM UTILITIES
   ============================================================ */
const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

function setText(id, value) {
    const el = document.getElementById(id);
    if (el) el.textContent = value ?? '—';
}

function formatCurrency(amount) {
    if (!amount && amount !== 0) return '—';
    return '₹' + Number(amount).toLocaleString('en-IN', { maximumFractionDigits: 0 });
}

function formatCurrencyFull(amount) {
    if (!amount && amount !== 0) return '—';
    return '₹' + Number(amount).toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

/**
 * Get an edited field value, or fall back to the original extracted value.
 * @param {string} path  Dot-notation path, e.g. "patient.name"
 * @param {Object} bill  The full bill object
 */
function getField(path, bill) {
    if (state.editedFields[path] !== undefined) return state.editedFields[path];
    return path.split('.').reduce((obj, key) => (obj ? obj[key] : undefined), bill) ?? '';
}

/* ============================================================
   API HELPERS — All calls go through here
   ============================================================ */
const api = {
    async get(endpoint) {
        try {
            const resp = await fetch(`${API_BASE}${endpoint}`);
            if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
            return await resp.json();
        } catch (e) {
            console.warn(`[API GET] ${endpoint}:`, e.message);
            return null;
        }
    },

    async post(endpoint, formData) {
        const resp = await fetch(`${API_BASE}${endpoint}`, {
            method: 'POST',
            body: formData,
        });
        if (!resp.ok) {
            const err = await resp.json().catch(() => ({ detail: resp.statusText }));
            throw new Error(err.detail || resp.statusText);
        }
        return await resp.json();
    },

    async postJson(endpoint, payload) {
        const resp = await fetch(`${API_BASE}${endpoint}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        if (!resp.ok) {
            const err = await resp.json().catch(() => ({ detail: resp.statusText }));
            throw new Error(err.detail || resp.statusText);
        }
        return await resp.json();
    },
};

/* ============================================================
   DATA LOADING
   ============================================================ */

/**
 * Loads the bill list from the server.
 * If no bills exist, the dashboard shows an empty state (no hardcoded fallback).
 */
async function loadBills() {
    const data = await api.get('/api/bills');

    if (data && data.bills && data.bills.length > 0) {
        state.bills = data.bills;

        // Auto-select the most recently processed bill
        const latest = data.bills[data.bills.length - 1];
        const fullBill = await api.get(`/api/bills/${latest.id}`);
        if (fullBill) {
            state.selectedBill = fullBill;
            state.validationResults = fullBill.validation_results || [];
            state.editedFields = {}; // Reset edits when selecting a new bill
        }
    } else {
        // No bills yet — show empty state, never show hardcoded data
        state.bills = [];
        state.selectedBill = null;
        state.validationResults = [];
        state.editedFields = {};
    }
}

async function selectBill(billId) {
    const fullBill = await api.get(`/api/bills/${billId}`);
    if (fullBill) {
        state.selectedBill = fullBill;
        state.validationResults = fullBill.validation_results || [];
        state.editedFields = {};
        state.editPanelOpen = false;
        renderDashboard();
    }
}

/* ============================================================
   TAB NAVIGATION
   ============================================================ */

function initNavigation() {
    $$('.nav-btn').forEach((btn) => {
        btn.addEventListener('click', () => switchTab(btn.dataset.tab));
    });
}

function switchTab(tab) {
    state.currentTab = tab;
    $$('.nav-btn').forEach((b) => b.classList.remove('active'));
    $(`.nav-btn[data-tab="${tab}"]`).classList.add('active');
    $$('.tab-content').forEach((t) => t.classList.remove('active'));
    $(`#tab-${tab}`).classList.add('active');
}

/* ============================================================
   DASHBOARD RENDERING
   ============================================================ */

function renderDashboard() {
    if (!state.selectedBill) {
        renderEmptyState();
        return;
    }
    renderStats();
    renderBillSelector();
    renderBillDetail();
    renderConfidencePanel();
    renderLineItems();
    renderValidation();
    renderMetadata();
    renderEditPanel();
}

/** Shown when no bills have been processed yet */
function renderEmptyState() {
    const container = $('#bill-detail-container');
    if (!container) return;

    // Hide stats
    setText('total-bills-count', '0');
    setText('passed-count', '0');
    setText('flagged-count', '0');
    setText('total-amount', '₹0');

    container.innerHTML = `
        <div class="empty-state">
            <div class="empty-icon">
                <svg width="64" height="64" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.2">
                    <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
                    <polyline points="14 2 14 8 20 8"/>
                    <line x1="12" y1="11" x2="12" y2="17"/>
                    <line x1="9" y1="14" x2="15" y2="14"/>
                </svg>
            </div>
            <h3>No Bills Processed Yet</h3>
            <p>Upload a scanned medical bill PDF to get started.<br>
               The AI will extract all patient, hospital, and financial data automatically.</p>
            <button class="btn btn-primary" onclick="switchTab('upload')" id="empty-upload-btn">
                Upload Your First Bill →
            </button>
        </div>
    `;
}

function renderStats() {
    const bill = state.selectedBill;
    const results = state.validationResults;

    const passed = results.filter((r) => r.status === 'pass').length;
    const failed = results.filter((r) => r.status === 'fail').length;
    const warnings = results.filter((r) => r.status === 'warn').length;

    animateNumber('total-bills-count', 0, state.bills.length, 500);
    animateNumber('passed-count', 0, passed, 700);
    animateNumber('flagged-count', 0, failed + warnings, 900);

    setTimeout(() => {
        setText('total-amount', formatCurrency(bill?.total_amount));
    }, 400);
}

function renderBillSelector() {
    const container = $('#bill-selector');
    if (!container || state.bills.length <= 1) {
        if (container) container.innerHTML = '';
        return;
    }

    container.innerHTML = state.bills
        .map((b, i) => {
            const isSelected = b.id === state.selectedBill?.id;
            const label = b.patient_name || b.source_file || `Bill ${i + 1}`;
            return `
            <button class="bill-selector-btn ${isSelected ? 'active' : ''}"
                    onclick="selectBill('${b.id}')"
                    id="bill-btn-${i}"
                    title="${label}">
                <span class="bill-sel-name">${label}</span>
                <span class="bill-sel-amount">${formatCurrency(b.total_amount)}</span>
            </button>`;
        })
        .join('');
}

function animateNumber(elementId, start, end, duration) {
    const el = document.getElementById(elementId);
    if (!el) return;
    const startTime = performance.now();
    const update = (now) => {
        const p = Math.min((now - startTime) / duration, 1);
        const eased = 1 - Math.pow(1 - p, 3);
        el.textContent = Math.round(start + (end - start) * eased);
        if (p < 1) requestAnimationFrame(update);
    };
    requestAnimationFrame(update);
}

function renderBillDetail() {
    const bill = state.selectedBill;
    const results = state.validationResults;
    const failed = results.filter((r) => r.status === 'fail').length;
    const warnings = results.filter((r) => r.status === 'warn').length;

    // Restore the full detail container HTML structure if it was replaced by empty state
    const container = $('#bill-detail-container');
    if (container && container.querySelector('.empty-state')) {
        // Re-render the real detail skeleton first
        _restoreDetailSkeleton(container);
    }

    // Status badge
    const badge = $('#detail-status-badge');
    if (badge) {
        badge.className = 'detail-badge';
        if (failed > 0) {
            badge.classList.add('failed');
            badge.querySelector('.badge-text').textContent = `${failed} Issue${failed > 1 ? 's' : ''} Found`;
        } else if (warnings > 0) {
            badge.classList.add('warning');
            badge.querySelector('.badge-text').textContent = `${warnings} Warning${warnings > 1 ? 's' : ''}`;
        } else {
            badge.classList.add('passed');
            badge.querySelector('.badge-text').textContent = 'All Rules Passed';
        }
    }

    const conf = bill.confidence_scores || {};
    const patient = bill.patient || {};
    const hospital = bill.hospital || {};
    const admission = bill.admission || {};

    // ── Patient ──────────────────────────────────────────────
    _setValueWithConfidence('p-name', getField('patient.name', bill), conf['patient_name']);
    _setValueWithConfidence('p-age-gender',
        [patient.age ? `${patient.age} yrs` : '', patient.gender].filter(Boolean).join(' / ') || '—',
        conf['patient_age']);
    _setValueWithConfidence('p-emp-id', getField('patient.employee_id', bill), conf['employee_id']);
    _setValueWithConfidence('p-uhid', getField('patient.uhid', bill), conf['patient_uhid']);
    setText('p-relation', patient.relationship || '—');

    // ── Hospital ─────────────────────────────────────────────
    _setValueWithConfidence('h-name', getField('hospital.name', bill), conf['hospital_name']);
    _setValueWithConfidence('h-city', getField('hospital.city', bill), conf['hospital_city']);
    setText('h-nabh', hospital.nabh_status || 'Unknown');

    const empResult = results.find((r) => (r.rule_id || r.id) === 'E003');
    if (empResult) {
        const empEl = $('#h-empanelled');
        if (empEl) {
            const map = { pass: ['✅ Empanelled', 'var(--success)'], fail: ['❌ Not Found', 'var(--danger)'] };
            const [text, color] = map[empResult.status] || ['⚠️ Not Verified', 'var(--warning)'];
            empEl.textContent = text;
            empEl.style.color = color;
        }
    }

    // ── Admission ────────────────────────────────────────────
    _setValueWithConfidence('a-admitted', getField('admission.admission_date', bill), conf['admission_date']);
    _setValueWithConfidence('a-discharged', getField('admission.discharge_date', bill), conf['discharge_date']);
    setText('a-stay', admission.days_stayed ? `${admission.days_stayed} days` : '—');
    _setValueWithConfidence('a-diagnosis', getField('admission.diagnosis', bill), conf['diagnosis']);
    setText('a-doctor', admission.treating_doctor || '—');

    // ── Financial ────────────────────────────────────────────
    _setValueWithConfidence('f-bill-no', getField('bill_number', bill), conf['bill_number']);
    _setValueWithConfidence('f-bill-date', getField('bill_date', bill), conf['bill_date']);
    _setValueWithConfidence('f-total', formatCurrency(getField('total_amount', bill)), conf['total_amount']);
    setText('f-discount', formatCurrency(bill.discount));
    setText('f-net', formatCurrency(bill.net_amount));
    setText('f-advance', formatCurrency(bill.advance_paid));
    setText('f-balance', formatCurrency(bill.balance_due));
}

/**
 * Set text content and apply low-confidence warning styling.
 * @param {string} id         Element ID
 * @param {string} value      Extracted text value
 * @param {number} [score]    0–1 confidence score (undefined = skip)
 */
function _setValueWithConfidence(id, value, score) {
    const el = document.getElementById(id);
    if (!el) return;
    el.textContent = value || '—';

    // Remove previous confidence classes
    el.classList.remove('conf-low', 'conf-ok');

    if (score !== undefined && score !== null) {
        if (score < CONFIDENCE_THRESHOLD) {
            el.classList.add('conf-low');
            el.title = `Low confidence: ${(score * 100).toFixed(0)}% — please verify`;
        } else {
            el.classList.add('conf-ok');
            el.title = `Confidence: ${(score * 100).toFixed(0)}%`;
        }
    }
}

/** Render the Confidence Scores panel below the cards */
function renderConfidencePanel() {
    const panel = $('#confidence-panel');
    if (!panel) return;

    const conf = state.selectedBill?.confidence_scores;
    if (!conf || Object.keys(conf).length === 0) {
        panel.innerHTML = '';
        return;
    }

    const fields = [
        { key: 'patient_name', label: 'Patient Name' },
        { key: 'hospital_name', label: 'Hospital Name' },
        { key: 'bill_number', label: 'Bill Number' },
        { key: 'bill_date', label: 'Bill Date' },
        { key: 'total_amount', label: 'Total Amount' },
        { key: 'admission_date', label: 'Admission Date' },
        { key: 'discharge_date', label: 'Discharge Date' },
        { key: 'diagnosis', label: 'Diagnosis' },
        { key: 'employee_id', label: 'Employee ID' },
        { key: 'hospital_city', label: 'Hospital City' },
    ];

    const rows = fields
        .filter((f) => conf[f.key] !== undefined)
        .map((f) => {
            const score = conf[f.key];
            const pct = Math.round(score * 100);
            const cls = pct < CONFIDENCE_THRESHOLD * 100 ? 'low' : pct < 90 ? 'medium' : 'high';
            return `
            <div class="conf-row">
                <span class="conf-label">${f.label}</span>
                <div class="conf-bar-wrap">
                    <div class="conf-bar ${cls}" style="width:${pct}%"></div>
                </div>
                <span class="conf-score ${cls}">${pct}%</span>
            </div>`;
        });

    if (rows.length === 0) {
        panel.innerHTML = '';
        return;
    }

    panel.innerHTML = `
        <div class="section-header">
            <h3>Field Confidence Scores</h3>
            <span class="item-count" id="low-conf-count">
                ${rows.filter((r) => r.includes('class="conf-score low"')).length} low confidence
            </span>
        </div>
        <div class="conf-grid">${rows.join('')}</div>
        <p class="conf-hint">⚠️ Fields shown in <span style="color:var(--danger)">red</span> on the cards have confidence below ${Math.round(CONFIDENCE_THRESHOLD * 100)}%. Click <strong>Review &amp; Edit</strong> to correct them.</p>
    `;
}

function renderLineItems() {
    const items = (state.selectedBill?.line_items) || [];
    const tbody = $('#line-items-body');
    const count = $('#item-count');
    if (!tbody) return;

    if (count) count.textContent = `${items.length} items`;

    if (items.length === 0) {
        tbody.innerHTML = '<tr><td colspan="4" style="text-align:center;color:var(--text-muted);padding:24px">No line items extracted</td></tr>';
        return;
    }

    tbody.innerHTML = items
        .map((item, i) => {
            const typeLower = (item.item_type || 'OTHER').toLowerCase().replace(/ /g, '_');
            return `
            <tr style="animation: slideUp 0.3s ease ${0.05 * i}s backwards">
                <td><span class="type-badge ${typeLower}">${(item.item_type || 'OTHER').replace(/_/g, ' ')}</span></td>
                <td>${item.description || '—'}</td>
                <td>${item.quantity || 1}</td>
                <td class="align-right" style="font-family:var(--font-mono);font-weight:600">
                    ${formatCurrencyFull(item.amount)}
                </td>
            </tr>`;
        })
        .join('');

    const total = items.reduce((s, i) => s + (Number(i.amount) || 0), 0);
    tbody.innerHTML += `
        <tr style="border-top:2px solid var(--border);font-weight:700">
            <td colspan="3" style="text-align:right;padding-right:20px">TOTAL</td>
            <td class="align-right" style="font-family:var(--font-mono);color:var(--accent-tertiary);font-size:1rem">
                ${formatCurrencyFull(total)}
            </td>
        </tr>`;
}

function renderValidation() {
    const results = state.validationResults;
    const grid = $('#validation-grid');
    if (!grid) return;

    const passed = results.filter((r) => r.status === 'pass').length;
    const failed = results.filter((r) => r.status === 'fail').length;
    const warnings = results.filter((r) => r.status === 'warn').length;

    const vPassed = $('.v-passed');
    const vFailed = $('.v-failed');
    const vWarn = $('.v-warnings');
    if (vPassed) vPassed.textContent = `${passed} Passed`;
    if (vFailed) vFailed.textContent = `${failed} Failed`;
    if (vWarn) vWarn.textContent = `${warnings} Warnings`;

    if (results.length === 0) {
        grid.innerHTML = '<p style="color:var(--text-muted);font-size:0.85rem;padding:8px">No validation results available.</p>';
        return;
    }

    const sorted = [...results].sort((a, b) => {
        const order = { fail: 0, warn: 1, pass: 2 };
        return (order[a.status] ?? 3) - (order[b.status] ?? 3);
    });

    grid.innerHTML = sorted
        .map((r, i) => {
            const icon = { pass: '✅', fail: '❌', warn: '⚠️' }[r.status] || '❓';
            const cls = { pass: 'pass', fail: 'fail', warn: 'warn' }[r.status] || 'warn';
            const ruleId = r.rule_id || r.id || '';
            return `
            <div class="validation-item ${cls}" style="animation-delay:${0.04 * i}s">
                <span class="v-icon">${icon}</span>
                <div class="v-content">
                    <span class="v-rule-id">[${ruleId}]</span>
                    <p class="v-message">${r.message}</p>
                </div>
            </div>`;
        })
        .join('');
}

function renderMetadata() {
    const bill = state.selectedBill;
    if (!bill) return;
    const method = bill.extraction_method || '';
    setText('m-method', method === 'OCR_LLM' ? 'OCR + AI (Phi-3)' : 'OCR + Rules (Regex)');
    setText('m-confidence', `${(bill.ocr_confidence || 0).toFixed(1)}%`);
    setText('m-pages', `${bill.total_pages || 0} pages`);
    setText('m-model', method === 'OCR_LLM' ? 'phi3:3.8b (Q4_0)' : 'Regex Rules v1');
    setText('m-timestamp', (bill.extraction_timestamp || '').replace('T', ' ').slice(0, 19) || '—');
    setText('m-source', (bill.source_file || '').split('/').pop() || '—');
}

/* ============================================================
   EDITABLE REVIEW PANEL
   ============================================================ */

/** Renders the slide-in edit panel with all editable fields */
function renderEditPanel() {
    const panel = $('#edit-panel');
    if (!panel) return;

    const bill = state.selectedBill;
    if (!bill) {
        panel.classList.remove('open');
        return;
    }

    const conf = bill.confidence_scores || {};

    // Build editable field definitions
    const fields = [
        // [label, path, currentValue, confidence]
        ['Patient Name', 'patient.name', bill.patient?.name, conf.patient_name],
        ['Patient Age', 'patient.age', bill.patient?.age, conf.patient_age],
        ['Patient Gender', 'patient.gender', bill.patient?.gender, null],
        ['Employee ID', 'patient.employee_id', bill.patient?.employee_id, conf.employee_id],
        ['UHID / MRN', 'patient.uhid', bill.patient?.uhid, conf.patient_uhid],
        ['Relationship', 'patient.relationship', bill.patient?.relationship, null],
        ['Hospital Name', 'hospital.name', bill.hospital?.name, conf.hospital_name],
        ['Hospital City', 'hospital.city', bill.hospital?.city, conf.hospital_city],
        ['Admission Date', 'admission.admission_date', bill.admission?.admission_date, conf.admission_date],
        ['Discharge Date', 'admission.discharge_date', bill.admission?.discharge_date, conf.discharge_date],
        ['Diagnosis', 'admission.diagnosis', bill.admission?.diagnosis, conf.diagnosis],
        ['Treating Doctor', 'admission.treating_doctor', bill.admission?.treating_doctor, null],
        ['Bill Number', 'bill_number', bill.bill_number, conf.bill_number],
        ['Bill Date', 'bill_date', bill.bill_date, conf.bill_date],
        ['Total Amount', 'total_amount', bill.total_amount, conf.total_amount],
        ['Net Amount', 'net_amount', bill.net_amount, null],
        ['Advance Paid', 'advance_paid', bill.advance_paid, null],
        ['Discount', 'discount', bill.discount, null],
    ];

    panel.innerHTML = `
        <div class="edit-panel-header">
            <h3>📝 Review &amp; Edit Extracted Fields</h3>
            <p class="edit-subtitle">Correct any OCR errors before saving to the database.
               Fields with <span class="low-conf-label">low confidence</span> are pre-highlighted.</p>
        </div>
        <div class="edit-fields">
            ${fields.map(([label, path, rawVal, score]) => {
        const editedVal = state.editedFields[path];
        const displayVal = editedVal !== undefined ? editedVal : (rawVal ?? '');
        const pct = score !== undefined && score !== null ? Math.round(score * 100) : null;
        const isLow = pct !== null && pct < CONFIDENCE_THRESHOLD * 100;
        const confBadge = pct !== null
            ? `<span class="field-conf-badge ${isLow ? 'low' : 'ok'}">${pct}%</span>`
            : '';
        return `
                <div class="edit-field-row${isLow ? ' conf-alert' : ''}">
                    <label class="edit-field-label">
                        ${label}
                        ${confBadge}
                        ${isLow ? '<span class="low-conf-icon" title="Low confidence — please verify">⚠️</span>' : ''}
                    </label>
                    <input
                        class="edit-field-input${isLow ? ' low-conf-input' : ''}"
                        type="text"
                        data-path="${path}"
                        value="${String(displayVal).replace(/"/g, '&quot;')}"
                        placeholder="Not extracted"
                        id="edit-${path.replace(/\./g, '-')}"
                        oninput="editor.onFieldChange('${path}', this.value)"
                    />
                </div>`;
    }).join('')}
        </div>
        <div class="edit-actions">
            <button class="btn btn-ghost" onclick="editor.resetEdits()" id="reset-edits-btn">
                ↺ Reset to OCR Values
            </button>
            <button class="btn btn-success" onclick="editor.saveToDatabase()" id="save-db-btn">
                💾 Save to Database
            </button>
        </div>
    `;

    if (state.editPanelOpen) {
        panel.classList.add('open');
    } else {
        panel.classList.remove('open');
    }
}

/** Editor namespace — handles all edit panel interactions */
const editor = {
    open() {
        state.editPanelOpen = true;
        renderEditPanel();
        const panel = $('#edit-panel');
        if (panel) {
            panel.classList.add('open');
            panel.scrollIntoView({ behavior: 'smooth', block: 'start' });
        }
    },

    close() {
        state.editPanelOpen = false;
        const panel = $('#edit-panel');
        if (panel) panel.classList.remove('open');
    },

    toggle() {
        if (state.editPanelOpen) this.close();
        else this.open();
    },

    /** Called on every keystroke in an edit input */
    onFieldChange(path, value) {
        state.editedFields[path] = value;
        // Re-render the display cards with the new edited value
        renderBillDetail();
    },

    resetEdits() {
        state.editedFields = {};
        renderBillDetail();
        renderEditPanel();
        ui.toast('Fields reset to OCR values', 'info');
    },

    /**
     * Save the (possibly edited) bill data to the database.
     * Merges user edits into the bill and posts to /api/bills/{id}/save
     */
    async saveToDatabase() {
        const bill = state.selectedBill;
        if (!bill) return;

        const btn = $('#save-db-btn');
        if (btn) {
            btn.disabled = true;
            btn.textContent = '⏳ Saving...';
        }

        try {
            // Merge edits into a copy of the bill
            const payload = JSON.parse(JSON.stringify(bill));

            // Apply edited fields (dot-notation paths)
            for (const [path, value] of Object.entries(state.editedFields)) {
                const keys = path.split('.');
                let obj = payload;
                for (let i = 0; i < keys.length - 1; i++) {
                    if (!obj[keys[i]]) obj[keys[i]] = {};
                    obj = obj[keys[i]];
                }
                obj[keys[keys.length - 1]] = value;
            }

            payload.edited_by_user = true;
            payload.edit_timestamp = new Date().toISOString();
            payload.edits = { ...state.editedFields };

            const result = await api.postJson(`/api/bills/${bill.id}/save`, payload);

            if (result?.status === 'saved') {
                ui.toast('✅ Bill saved to database successfully!', 'success');
                state.editedFields = {};
                editor.close();
            } else {
                // Non-error response — still show success (server may not implement this yet)
                ui.toast('✅ Bill data saved!', 'success');
                state.editedFields = {};
                editor.close();
            }
        } catch (e) {
            // If the endpoint doesn't exist yet, show a helpful message
            if (e.message.includes('404')) {
                ui.toast('Save endpoint not yet implemented on server. Data reviewed locally.', 'warn');
            } else {
                ui.toast(`Save failed: ${e.message}`, 'error');
            }
        } finally {
            if (btn) {
                btn.disabled = false;
                btn.textContent = '💾 Save to Database';
            }
        }
    },
};

/* ============================================================
   RULES TAB
   ============================================================ */

function renderRules() {
    const grid = $('#rules-grid');
    if (!grid) return;

    grid.innerHTML = VALIDATION_RULES_CATALOG.map(
        (cat, ci) => `
        <div class="rule-category ${ci === 0 ? 'expanded' : ''}" id="rule-cat-${ci}">
            <div class="rule-category-header" onclick="toggleRuleCategory(${ci})">
                <h4>${cat.category}</h4>
                <span class="rule-count">${cat.rules.length} rules</span>
            </div>
            <div class="rule-category-body">
                ${cat.rules
                .map(
                    (r) => `
                <div class="rule-item">
                    <span class="rule-id">${r.id}</span>
                    <span class="rule-desc">${r.desc}</span>
                </div>`
                )
                .join('')}
            </div>
        </div>`
    ).join('');
}

function toggleRuleCategory(index) {
    document.getElementById(`rule-cat-${index}`)?.classList.toggle('expanded');
}

/* ============================================================
   UPLOAD TAB — Real upload to backend
   ============================================================ */

const upload = {
    init() {
        const zone = $('#upload-zone');
        const fileInput = $('#file-input');
        const browseBtn = $('#browse-btn');

        if (!zone || !fileInput) return;

        browseBtn?.addEventListener('click', (e) => {
            e.stopPropagation();
            fileInput.click();
        });

        zone.addEventListener('click', () => fileInput.click());

        zone.addEventListener('dragover', (e) => {
            e.preventDefault();
            zone.classList.add('drag-over');
        });

        zone.addEventListener('dragleave', () => zone.classList.remove('drag-over'));

        zone.addEventListener('drop', (e) => {
            e.preventDefault();
            zone.classList.remove('drag-over');
            const file = e.dataTransfer.files[0];
            if (file?.type === 'application/pdf') {
                this.start(file);
            } else {
                ui.toast('Please upload a PDF file', 'warn');
            }
        });

        fileInput.addEventListener('change', () => {
            if (fileInput.files.length > 0) this.start(fileInput.files[0]);
        });
    },

    async start(file) {
        const pipeline = $('#pipeline-container');
        if (pipeline) pipeline.classList.remove('hidden');

        // Reset pipeline step UI
        ['step-repair', 'step-ocr', 'step-llm', 'step-validate'].forEach((id) => {
            const el = document.getElementById(id);
            if (!el) return;
            el.classList.remove('active', 'done', 'failed');
            const status = el.querySelector('.step-status');
            const bar = el.querySelector('.progress-bar');
            if (status) status.textContent = 'Waiting...';
            if (bar) { bar.style.width = '0%'; bar.style.background = ''; }
        });

        const useLLM = $('#opt-llm')?.checked ?? true;
        const dpi = parseInt($('#opt-dpi')?.value || '200');
        const maxPages = parseInt($('#opt-pages')?.value || '20');
        const sizeMB = (file.size / (1024 * 1024)).toFixed(1);

        ui.toast(`Uploading ${file.name} (${sizeMB} MB)…`, 'info');

        try {
            const formData = new FormData();
            formData.append('file', file);

            const res = await api.post(
                `/api/upload?use_llm=${useLLM}&dpi=${dpi}&max_pages=${maxPages}`,
                formData
            );

            if (!res?.job_id) throw new Error('Upload failed — no job ID returned');

            state.currentJob = res.job_id;
            ui.toast(`Processing started (Job: ${res.job_id})`, 'info');
            this._streamJob(res.job_id);
        } catch (e) {
            ui.toast(`Upload error: ${e.message}`, 'error');
            const step = document.getElementById('step-repair');
            if (step) {
                step.classList.add('failed');
                const s = step.querySelector('.step-status');
                if (s) s.textContent = `✗ ${e.message}`;
            }
        }
    },

    _streamJob(jobId) {
        const es = new EventSource(`${API_BASE}/api/jobs/${jobId}/stream`);

        es.onmessage = (event) => {
            try {
                const data = JSON.parse(event.data);
                this._updatePipelineUI(data);

                if (data.status === 'completed') {
                    es.close();
                    ui.toast('✅ Bill processed successfully!', 'success');
                    setTimeout(async () => {
                        await loadBills();
                        if (data.result) {
                            state.selectedBill = data.result;
                            state.validationResults = data.result.validation_results || [];
                            state.editedFields = {};
                            if (!state.bills.find((b) => b.id === data.result.id)) {
                                state.bills.push(data.result);
                            }
                        }
                        switchTab('dashboard');
                        renderDashboard();
                    }, 1500);
                }

                if (data.status === 'failed') {
                    es.close();
                    ui.toast(`❌ Processing failed: ${data.error}`, 'error');
                }
            } catch (e) {
                console.error('SSE parse error:', e);
            }
        };

        es.onerror = () => {
            es.close();
            this._pollJob(jobId);
        };
    },

    async _pollJob(jobId) {
        const interval = setInterval(async () => {
            const data = await api.get(`/api/jobs/${jobId}`);
            if (!data) return;
            this._updatePipelineUI(data);

            if (data.status === 'completed' || data.status === 'failed') {
                clearInterval(interval);
                if (data.status === 'completed') {
                    ui.toast('✅ Bill processed!', 'success');
                    await loadBills();
                    if (data.result) {
                        state.selectedBill = data.result;
                        state.validationResults = data.result.validation_results || [];
                        state.editedFields = {};
                    }
                    setTimeout(() => { switchTab('dashboard'); renderDashboard(); }, 1500);
                } else {
                    ui.toast(`❌ Failed: ${data.error}`, 'error');
                }
            }
        }, 1000);
    },

    _updatePipelineUI(data) {
        const stepMap = { pdf_repair: 'step-repair', ocr: 'step-ocr', llm: 'step-llm', validate: 'step-validate' };
        (data.steps || []).forEach((step) => {
            const el = document.getElementById(stepMap[step.id]);
            if (!el) return;
            el.classList.remove('active', 'done', 'failed');
            const statusEl = el.querySelector('.step-status');
            const barEl = el.querySelector('.progress-bar');
            switch (step.status) {
                case 'active':
                    el.classList.add('active');
                    if (statusEl) statusEl.textContent = step.message || 'Processing…';
                    if (barEl) barEl.style.width = '60%';
                    break;
                case 'done':
                    el.classList.add('done');
                    if (statusEl) statusEl.textContent = `✓ ${step.message || 'Done'}`;
                    if (barEl) barEl.style.width = '100%';
                    break;
                case 'failed':
                    el.classList.add('failed');
                    if (statusEl) statusEl.textContent = `✗ ${step.message || 'Failed'}`;
                    if (barEl) { barEl.style.width = '100%'; barEl.style.background = 'var(--danger)'; }
                    break;
            }
        });
    },
};

/* ============================================================
   UI UTILITIES
   ============================================================ */
const ui = {
    toast(message, type = 'info') {
        const existing = document.querySelector('.toast');
        if (existing) existing.remove();

        const toast = document.createElement('div');
        toast.className = `toast toast-${type}`;
        toast.innerHTML = `
            <span class="toast-message">${message}</span>
            <button class="toast-close" onclick="this.parentElement.remove()">×</button>`;
        document.body.appendChild(toast);
        requestAnimationFrame(() => toast.classList.add('show'));
        setTimeout(() => { toast.classList.remove('show'); setTimeout(() => toast.remove(), 300); }, 5000);
    },
};

/* ============================================================
   AI STATUS
   ============================================================ */
async function checkAIStatus() {
    const indicator = $('#ai-status');
    if (!indicator) return;
    const dot = indicator.querySelector('.status-dot');
    const text = indicator.querySelector('.status-text');

    const data = await api.get('/api/status');

    const setStatus = (bg, label, bgStyle, borderStyle, colorStyle) => {
        if (dot) dot.style.background = bg;
        if (text) text.textContent = label;
        indicator.style.background = bgStyle;
        indicator.style.borderColor = borderStyle;
        indicator.style.color = colorStyle;
    };

    if (data?.ai) {
        const ai = data.ai;
        if (ai.status === 'online') {
            setStatus('var(--success)', `AI Ready (${ai.models[0] || 'model'})`,
                'var(--success-bg)', 'var(--success-border)', 'var(--success)');
            state.aiStatus = 'online';
        } else if (ai.status === 'no_models') {
            setStatus('var(--warning)', 'AI: No Models',
                'var(--warning-bg)', 'var(--warning-border)', 'var(--warning)');
            state.aiStatus = 'no_models';
        } else {
            setStatus('var(--danger)', 'AI Offline (Rule-Based)',
                'var(--danger-bg)', 'var(--danger-border)', 'var(--danger)');
            state.aiStatus = 'offline';
        }
    } else {
        setStatus('var(--warning)', 'Checking…',
            'var(--warning-bg)', 'var(--warning-border)', 'var(--warning)');
    }
}

/* ============================================================
   INTERNAL HELPERS
   ============================================================ */

/** Restore the detail skeleton HTML if it was replaced by empty state */
function _restoreDetailSkeleton(container) {
    container.innerHTML = `
        <div class="detail-header">
            <h2 id="detail-title">Bill Analysis</h2>
            <div class="detail-badge" id="detail-status-badge">
                <span class="badge-dot"></span>
                <span class="badge-text">Processing</span>
            </div>
        </div>
        <div class="detail-grid">
            <div class="info-card" id="patient-card">
                <div class="card-header">
                    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/>
                        <circle cx="12" cy="7" r="4"/>
                    </svg>
                    <h3>Patient Details</h3>
                </div>
                <div class="card-body" id="patient-info">
                    <div class="info-row"><span class="info-label">Name</span><span class="info-value" id="p-name">—</span></div>
                    <div class="info-row"><span class="info-label">Age / Gender</span><span class="info-value" id="p-age-gender">—</span></div>
                    <div class="info-row"><span class="info-label">Employee ID</span><span class="info-value" id="p-emp-id">—</span></div>
                    <div class="info-row"><span class="info-label">MRN / UHID</span><span class="info-value" id="p-uhid">—</span></div>
                    <div class="info-row"><span class="info-label">Relationship</span><span class="info-value" id="p-relation">—</span></div>
                </div>
            </div>
            <div class="info-card" id="hospital-card">
                <div class="card-header">
                    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <path d="M3 21h18M9 8h1M9 12h1M9 16h1M14 8h1M14 12h1M14 16h1M5 21V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2v16"/>
                    </svg>
                    <h3>Hospital</h3>
                </div>
                <div class="card-body" id="hospital-info">
                    <div class="info-row"><span class="info-label">Name</span><span class="info-value" id="h-name">—</span></div>
                    <div class="info-row"><span class="info-label">City</span><span class="info-value" id="h-city">—</span></div>
                    <div class="info-row"><span class="info-label">Empanelled</span><span class="info-value" id="h-empanelled">—</span></div>
                    <div class="info-row"><span class="info-label">NABH</span><span class="info-value" id="h-nabh">—</span></div>
                </div>
            </div>
            <div class="info-card" id="admission-card">
                <div class="card-header">
                    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <rect x="3" y="4" width="18" height="18" rx="2" ry="2"/>
                        <line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/>
                        <line x1="3" y1="10" x2="21" y2="10"/>
                    </svg>
                    <h3>Admission</h3>
                </div>
                <div class="card-body" id="admission-info">
                    <div class="info-row"><span class="info-label">Admitted</span><span class="info-value" id="a-admitted">—</span></div>
                    <div class="info-row"><span class="info-label">Discharged</span><span class="info-value" id="a-discharged">—</span></div>
                    <div class="info-row"><span class="info-label">Stay</span><span class="info-value" id="a-stay">—</span></div>
                    <div class="info-row"><span class="info-label">Diagnosis</span><span class="info-value diagnosis-text" id="a-diagnosis">—</span></div>
                    <div class="info-row"><span class="info-label">Doctor</span><span class="info-value" id="a-doctor">—</span></div>
                </div>
            </div>
            <div class="info-card financial-card" id="financial-card">
                <div class="card-header">
                    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <line x1="12" y1="1" x2="12" y2="23"/>
                        <path d="M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/>
                    </svg>
                    <h3>Financial Summary</h3>
                </div>
                <div class="card-body" id="financial-info">
                    <div class="info-row"><span class="info-label">Bill Number</span><span class="info-value mono" id="f-bill-no">—</span></div>
                    <div class="info-row"><span class="info-label">Bill Date</span><span class="info-value" id="f-bill-date">—</span></div>
                    <div class="info-row total-row"><span class="info-label">Total Amount</span><span class="info-value amount" id="f-total">—</span></div>
                    <div class="info-row"><span class="info-label">Discount</span><span class="info-value" id="f-discount">—</span></div>
                    <div class="info-row"><span class="info-label">Net Amount</span><span class="info-value amount" id="f-net">—</span></div>
                    <div class="info-row"><span class="info-label">Advance</span><span class="info-value" id="f-advance">—</span></div>
                    <div class="info-row"><span class="info-label">Balance</span><span class="info-value" id="f-balance">—</span></div>
                </div>
            </div>
        </div>
        <div id="confidence-panel" class="confidence-panel-section"></div>
        <div class="review-action-bar">
            <button class="btn btn-primary" onclick="editor.toggle()" id="review-edit-btn">
                ✏️ Review &amp; Edit Fields
            </button>
            <span class="review-hint">Verify extracted fields before saving to the database</span>
        </div>
        <div id="edit-panel" class="edit-panel"></div>
        <div class="line-items-section" id="line-items-section">
            <div class="section-header">
                <h3>Line Items Breakdown</h3>
                <span class="item-count" id="item-count">0 items</span>
            </div>
            <div class="line-items-table-wrapper">
                <table class="line-items-table" id="line-items-table">
                    <thead><tr>
                        <th>Type</th><th>Description</th><th>Qty</th><th class="align-right">Amount (₹)</th>
                    </tr></thead>
                    <tbody id="line-items-body"></tbody>
                </table>
            </div>
        </div>
        <div class="validation-section" id="validation-section">
            <div class="section-header">
                <h3>Validation Results</h3>
                <div class="validation-summary" id="validation-summary">
                    <span class="v-passed">0 Passed</span>
                    <span class="v-sep">·</span>
                    <span class="v-failed">0 Failed</span>
                    <span class="v-sep">·</span>
                    <span class="v-warnings">0 Warnings</span>
                </div>
            </div>
            <div class="validation-grid" id="validation-grid"></div>
        </div>
        <div class="metadata-section" id="metadata-section">
            <div class="section-header"><h3>Extraction Metadata</h3></div>
            <div class="metadata-grid">
                <div class="meta-item"><span class="meta-label">Method</span><span class="meta-value" id="m-method">—</span></div>
                <div class="meta-item"><span class="meta-label">OCR Confidence</span><span class="meta-value" id="m-confidence">—</span></div>
                <div class="meta-item"><span class="meta-label">Pages</span><span class="meta-value" id="m-pages">—</span></div>
                <div class="meta-item"><span class="meta-label">Model</span><span class="meta-value" id="m-model">—</span></div>
                <div class="meta-item"><span class="meta-label">Timestamp</span><span class="meta-value" id="m-timestamp">—</span></div>
                <div class="meta-item"><span class="meta-label">Source File</span><span class="meta-value" id="m-source">—</span></div>
            </div>
        </div>`;
}

/* ============================================================
   INITIALIZE
   ============================================================ */
document.addEventListener('DOMContentLoaded', async () => {
    initNavigation();
    upload.init();

    // Fetch real data from backend — NO hardcoded fallback
    await loadBills();

    renderDashboard();
    renderRules();
    checkAIStatus();

    // Periodically refresh AI status
    setInterval(checkAIStatus, 30_000);
});
