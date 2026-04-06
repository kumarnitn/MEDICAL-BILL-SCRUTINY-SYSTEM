# 🏥 CGHS Bill Auditor

> **Automated medical bill verification system that validates hospital claims against CGHS (Central Government Health Scheme) approved rates and flags fraudulent or non-compliant entries.**

---

## 📌 Overview

Medical billing fraud and overcharging are persistent challenges in healthcare reimbursement. The **CGHS Bill Auditor** is an intelligent document analysis system that ingests hospital bills, cross-references every line item against the official CGHS rate schedule, and automatically flags discrepancies, overcharges, and rule violations — saving time for medical officers, PAO clerks, and CGHS beneficiaries alike.

---

## ✨ Key Features

- 📄 **Bill Ingestion** — Accepts scanned or digital hospital bills as input (PDF / image / structured data)
- 🔍 **Line-by-line Rate Verification** — Checks each procedure, investigation, consultation, and medicine against the latest CGHS approved rate list
- 🚨 **Automatic Flagging** — Highlights amounts that exceed CGHS-permissible limits
- 📋 **Rule Compliance Check** — Validates claims against CGHS rules (e.g., package rates, non-admissible items, duplicate billing, referral requirements)
- 📊 **Audit Report Generation** — Produces a structured report listing approved, flagged, and rejected items with reasons
- 💰 **Excess Amount Calculation** — Computes the total overcharged amount per claim
- 🏷️ **Claim Classification** — Categorizes each item as ✅ Approved / ⚠️ Overcharged / ❌ Non-Admissible / 🔁 Duplicate

---

## 🛠️ How It Works

```
Input Bill (PDF/Image/Text)
        │
        ▼
 ┌─────────────────┐
 │  Bill Parser    │  ← Extracts line items, amounts, procedure codes
 └────────┬────────┘
          │
          ▼
 ┌─────────────────┐
 │  CGHS Rate DB   │  ← Lookups against official CGHS rate schedule
 └────────┬────────┘
          │
          ▼
 ┌─────────────────┐
 │  Rules Engine   │  ← Applies CGHS billing rules & admissibility checks
 └────────┬────────┘
          │
          ▼
 ┌─────────────────┐
 │  Audit Report   │  ← Flagged claims, excess amounts, compliance summary
 └─────────────────┘
```

---

## 🚩 What Gets Flagged

| Flag Type | Description |
|-----------|-------------|
| 💸 **Overcharged Amount** | Billed amount exceeds CGHS approved rate for the procedure |
| ❌ **Non-Admissible Item** | Item not covered under CGHS scheme |
| 🔁 **Duplicate Billing** | Same procedure billed multiple times on the same date |
| 📦 **Package Rate Violation** | Individual items billed separately when a CGHS package rate applies |
| 📎 **Missing Referral** | Specialist or investigation done without valid CGHS referral |
| 🏨 **Ward Entitlement Breach** | Room/ward category billed beyond beneficiary's entitlement |
| 💊 **Non-Generic Medicine** | Branded medicine billed where generic equivalent is mandated |
| 📅 **Date Anomaly** | Service dates outside the admission period or overlapping |

---

## 📂 Project Structure

```
cghs-bill-auditor/
├── input/                  # Sample bills for testing
├── data/
│   ├── cghs_rates.json     # CGHS approved rate schedule
│   └── rules.json          # CGHS billing rules & admissibility criteria
├── src/
│   ├── parser.py           # Bill extraction & preprocessing
│   ├── rate_checker.py     # Rate comparison engine
│   ├── rules_engine.py     # Compliance rule validation
│   └── report_generator.py # Audit report output
├── output/                 # Generated audit reports
├── tests/                  # Unit & integration tests
├── requirements.txt
└── README.md
```

---

## 🚀 Getting Started

### Prerequisites

- Python 3.9+
- pip

### Installation

```bash
# Clone the repository
git clone https://github.com/your-username/cghs-bill-auditor.git
cd cghs-bill-auditor

# Install dependencies
pip install -r requirements.txt
```

### Usage

```bash
# Run audit on a single bill
python src/main.py --input input/sample_bill.pdf

# Run audit with detailed report output
python src/main.py --input input/sample_bill.pdf --output output/report.json --verbose

# Batch process multiple bills
python src/main.py --batch input/bills_folder/
```

### Sample Output

```json
{
  "bill_id": "AIIMS-2024-00123",
  "patient": "CGHS Beneficiary",
  "total_billed": 85400,
  "cghs_admissible": 61200,
  "excess_claimed": 24200,
  "flagged_items": [
    {
      "item": "MRI Brain with Contrast",
      "billed_amount": 9500,
      "cghs_rate": 6800,
      "excess": 2700,
      "flag": "OVERCHARGED"
    },
    {
      "item": "Private Ward (Deluxe)",
      "billed_amount": 4500,
      "cghs_rate": 0,
      "excess": 4500,
      "flag": "WARD_ENTITLEMENT_BREACH"
    }
  ],
  "status": "FLAGGED",
  "recommendation": "Reimbursement recommended for ₹61,200 only. Refer for further scrutiny."
}
```

---

## 📊 Use Cases

- **CGHS Wellness Centres** — Automated pre-approval and post-audit of empanelled hospital bills
- **PAO / Accounts Offices** — Faster bill processing with reduced manual verification effort  
- **Hospital Billing Departments** — Self-audit before submission to avoid rejections
- **Medical Officers** — Objective second opinion on bill scrutiny
- **Beneficiaries** — Transparency into what is and isn't covered

---

## 🗄️ Data Sources

- CGHS Rate List (latest revision) — [cghs.nic.in](https://cghs.nic.in)
- CGHS Rules & Regulations — Ministry of Health & Family Welfare, GoI
- Admissibility Guidelines — CGHS Circulars & Office Memoranda

> ⚠️ Rate schedules must be kept up to date. The system includes a versioned rate database; always ensure it reflects the latest CGHS revision before auditing.

---

## 🔮 Roadmap

- [ ] OCR support for scanned paper bills
- [ ] Web interface for non-technical users
- [ ] Integration with CGHS hospital empanelment portal
- [ ] Multi-city rate differentiation (Delhi, Mumbai, Chennai, etc.)
- [ ] ML-based anomaly detection for unusual billing patterns
- [ ] WhatsApp/email report delivery

---

## 🤝 Contributing

Contributions are welcome! If you have access to updated CGHS rate schedules, new rule sets, or want to improve the parser for specific hospital bill formats, please open a pull request or issue.

1. Fork the repository
2. Create your feature branch (`git checkout -b feature/add-new-rules`)
3. Commit your changes (`git commit -m 'Add package rate validation for cardiology'`)
4. Push to the branch (`git push origin feature/add-new-rules`)
5. Open a Pull Request

---

## ⚖️ Disclaimer

This tool is intended to **assist** in the verification of CGHS claims and is not a substitute for official CGHS medical officer scrutiny. Final reimbursement decisions rest with the competent authority as per CGHS rules. Rate data must be validated against official CGHS publications before use.

---

## 📄 License

MIT License — see [LICENSE](LICENSE) for details.

---

## 👨‍💻 Author

Built with ❤️ to bring transparency and efficiency to CGHS healthcare reimbursement in India.

---

*If this tool helped you catch a fraudulent claim or saved hours of manual verification — give it a ⭐ on GitHub!*
