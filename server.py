#!/usr/bin/env python3
"""
MedBill AI — Backend Server
==============================
FastAPI server that:
  1. Serves the dashboard static files
  2. Provides REST API for bill upload & processing
  3. Runs OCR + LLM extraction pipeline
  4. Runs validation engine against extracted data
  5. Streams real-time progress via Server-Sent Events (SSE)
  6. Checks Ollama AI status
  7. Manages processed bill history

Start with:
    python3 server.py
    # or:  uvicorn server:app --host 0.0.0.0 --port 8080 --reload
"""

import os
import sys
import json
import time
import uuid
import sqlite3
import asyncio
import traceback
from datetime import datetime
from typing import Dict, List, Optional
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

# === Paths ===
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
DASHBOARD_DIR = os.path.join(PROJECT_DIR, 'dashboard')
UPLOADS_DIR = os.path.join(PROJECT_DIR, 'data', 'uploads')
OCR_OUTPUT_DIR = os.path.join(PROJECT_DIR, 'data', 'ocr_output')
DB_PATH = os.path.join(PROJECT_DIR, 'data', 'processed', 'medical_bills.db')
BILLS_JSON_DIR = os.path.join(PROJECT_DIR, 'data', 'processed', 'bills')

os.makedirs(UPLOADS_DIR, exist_ok=True)
os.makedirs(OCR_OUTPUT_DIR, exist_ok=True)
os.makedirs(BILLS_JSON_DIR, exist_ok=True)

# === Add scripts to path ===
sys.path.insert(0, os.path.join(PROJECT_DIR, 'scripts'))

# === FastAPI App ===
app = FastAPI(
    title="MedBill AI",
    description="Automated Medical Bill Validation System",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# === In-memory state ===
processing_jobs: Dict[str, dict] = {}
processed_bills: List[dict] = []

# Load any previously processed bills on startup
def _load_existing_bills():
    """Load previously processed bill JSONs from disk."""
    global processed_bills
    if os.path.exists(BILLS_JSON_DIR):
        for fname in sorted(os.listdir(BILLS_JSON_DIR)):
            if fname.endswith('.json'):
                try:
                    with open(os.path.join(BILLS_JSON_DIR, fname), 'r') as f:
                        bill_data = json.load(f)
                        processed_bills.append(bill_data)
                except Exception:
                    pass

_load_existing_bills()


# ============================================================================
# API Routes
# ============================================================================

@app.get("/api/status")
async def get_status():
    """Check system status including Ollama AI."""
    ai_status = await _check_ollama()
    
    db_exists = os.path.exists(DB_PATH)
    db_stats = {}
    if db_exists:
        try:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            cghs_count = c.execute("SELECT COUNT(*) FROM cghs_rates").fetchone()[0]
            hosp_count = c.execute("SELECT COUNT(*) FROM hospitals").fetchone()[0]
            db_stats = {"cghs_rates": cghs_count, "hospitals": hosp_count}
            conn.close()
        except Exception:
            db_stats = {"error": "Could not query database"}
    
    return {
        "status": "running",
        "timestamp": datetime.now().isoformat(),
        "ai": ai_status,
        "database": {
            "exists": db_exists,
            "path": DB_PATH,
            **db_stats,
        },
        "bills_processed": len(processed_bills),
    }


@app.get("/api/bills")
async def list_bills():
    """List all processed bills."""
    # Return summary of each bill (without heavy raw_ocr_text)
    summaries = []
    for bill in processed_bills:
        summary = {
            "id": bill.get("id", ""),
            "source_file": bill.get("source_file", ""),
            "total_pages": bill.get("total_pages", 0),
            "extraction_timestamp": bill.get("extraction_timestamp", ""),
            "patient_name": bill.get("patient", {}).get("name", ""),
            "hospital_name": bill.get("hospital", {}).get("name", ""),
            "total_amount": bill.get("total_amount", 0),
            "net_amount": bill.get("net_amount", 0),
            "extraction_method": bill.get("extraction_method", ""),
            "ocr_confidence": bill.get("ocr_confidence", 0),
            "validation_summary": bill.get("validation_summary", {}),
        }
        summaries.append(summary)
    return {"bills": summaries, "total": len(summaries)}


@app.get("/api/bills/{bill_id}")
async def get_bill(bill_id: str):
    """Get full detail of a processed bill including validation results."""
    for bill in processed_bills:
        if bill.get("id") == bill_id:
            # Return everything except raw_ocr_text (too large)
            result = {k: v for k, v in bill.items() if k != "raw_ocr_text"}
            return result
    raise HTTPException(status_code=404, detail=f"Bill {bill_id} not found")


@app.post("/api/upload")
async def upload_bill(
    file: UploadFile = File(...),
    use_llm: bool = Query(True, description="Use AI model for extraction"),
    dpi: int = Query(200, description="OCR resolution"),
    max_pages: int = Query(20, description="Max pages to process (0 = all)"),
):
    """Upload a PDF bill for processing. Returns a job ID for progress tracking."""
    if not file.filename.lower().endswith('.pdf'):
        raise HTTPException(status_code=400, detail="Only PDF files are accepted")
    
    # Save uploaded file
    file_id = str(uuid.uuid4())[:8]
    safe_name = file.filename.replace(' ', '_').replace('(', '').replace(')', '')
    saved_path = os.path.join(UPLOADS_DIR, f"{file_id}_{safe_name}")
    
    content = await file.read()
    with open(saved_path, 'wb') as f:
        f.write(content)
    
    file_size_mb = len(content) / (1024 * 1024)
    
    # Create job
    job_id = str(uuid.uuid4())[:12]
    processing_jobs[job_id] = {
        "id": job_id,
        "status": "queued",
        "filename": file.filename,
        "file_path": saved_path,
        "file_size_mb": round(file_size_mb, 2),
        "use_llm": use_llm,
        "dpi": dpi,
        "max_pages": max_pages,
        "created_at": datetime.now().isoformat(),
        "steps": [],
        "progress": 0,
        "result": None,
        "error": None,
    }
    
    # Start processing in background
    asyncio.create_task(_process_bill_async(job_id))
    
    return {
        "job_id": job_id,
        "filename": file.filename,
        "file_size_mb": round(file_size_mb, 2),
        "message": "Processing started",
    }


@app.get("/api/jobs/{job_id}")
async def get_job_status(job_id: str):
    """Get the current status of a processing job."""
    if job_id not in processing_jobs:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    
    job = processing_jobs[job_id]
    return {
        "id": job["id"],
        "status": job["status"],
        "filename": job["filename"],
        "progress": job["progress"],
        "steps": job["steps"],
        "error": job["error"],
        "result": job.get("result"),
    }


@app.get("/api/jobs/{job_id}/stream")
async def stream_job_progress(job_id: str):
    """Server-Sent Events stream for real-time progress updates."""
    if job_id not in processing_jobs:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    
    async def event_generator():
        last_step_count = 0
        last_status = ""
        
        while True:
            job = processing_jobs.get(job_id)
            if not job:
                break
            
            # Send update if something changed
            current_step_count = len(job["steps"])
            current_status = job["status"]
            
            if current_step_count != last_step_count or current_status != last_status:
                data = json.dumps({
                    "status": job["status"],
                    "progress": job["progress"],
                    "steps": job["steps"],
                    "error": job["error"],
                })
                yield f"data: {data}\n\n"
                last_step_count = current_step_count
                last_status = current_status
            
            if job["status"] in ("completed", "failed"):
                # Send final result
                if job.get("result"):
                    result_data = {k: v for k, v in job["result"].items() if k != "raw_ocr_text"}
                    yield f"data: {json.dumps({'status': 'completed', 'result': result_data})}\n\n"
                elif job.get("error"):
                    yield f"data: {json.dumps({'status': 'failed', 'error': job['error']})}\n\n"
                break
            
            await asyncio.sleep(0.5)
    
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )


@app.get("/api/cghs/search")
async def search_cghs(q: str = Query(..., min_length=2)):
    """Search CGHS rate card for procedure matching."""
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        
        # Use FTS5 if available
        try:
            rows = c.execute("""
                SELECT sr_no, procedure_name, non_nabh_rate, nabh_rate, category
                FROM cghs_rates_fts 
                WHERE cghs_rates_fts MATCH ?
                LIMIT 10
            """, (q,)).fetchall()
        except Exception:
            # Fall back to LIKE
            rows = c.execute("""
                SELECT sr_no, procedure_name, non_nabh_rate, nabh_rate, category
                FROM cghs_rates 
                WHERE procedure_name LIKE ?
                LIMIT 10
            """, (f'%{q}%',)).fetchall()
        
        conn.close()
        
        results = []
        for row in rows:
            results.append({
                "sr_no": row[0],
                "procedure_name": row[1],
                "non_nabh_rate": row[2],
                "nabh_rate": row[3],
                "category": row[4],
            })
        
        return {"query": q, "results": results, "count": len(results)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/hospitals/search")
async def search_hospitals(q: str = Query(..., min_length=2)):
    """Search empanelled hospital list."""
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        
        rows = c.execute("""
            SELECT hospital_name, city, state, nabh_status, empanelled_for
            FROM hospitals 
            WHERE hospital_name LIKE ? OR city LIKE ?
            LIMIT 10
        """, (f'%{q}%', f'%{q}%')).fetchall()
        
        conn.close()
        
        results = []
        for row in rows:
            results.append({
                "name": row[0],
                "city": row[1],
                "state": row[2],
                "nabh_status": row[3],
                "empanelled_for": row[4],
            })
        
        return {"query": q, "results": results, "count": len(results)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# Static Files & Dashboard
# ============================================================================

# Serve dashboard static files
app.mount("/static", StaticFiles(directory=DASHBOARD_DIR), name="static")

@app.get("/")
async def serve_dashboard():
    return FileResponse(os.path.join(DASHBOARD_DIR, 'index.html'))

@app.get("/styles.css")
async def serve_css():
    return FileResponse(os.path.join(DASHBOARD_DIR, 'styles.css'))

@app.get("/app.js")
async def serve_js():
    return FileResponse(
        os.path.join(DASHBOARD_DIR, 'app.js'),
        media_type='application/javascript'
    )


# ============================================================================
# Background Processing
# ============================================================================

async def _check_ollama() -> dict:
    """Check if Ollama is running and what models are available."""
    try:
        import requests
        resp = requests.get("http://localhost:11434/api/tags", timeout=2)
        if resp.status_code == 200:
            models = resp.json().get('models', [])
            model_names = [m['name'] for m in models]
            return {
                "status": "online" if models else "no_models",
                "models": model_names,
            }
    except Exception:
        pass
    return {"status": "offline", "models": []}


async def _process_bill_async(job_id: str):
    """Run the full bill processing pipeline in the background."""
    job = processing_jobs[job_id]
    job["status"] = "processing"
    
    try:
        # === Step 1: PDF Repair / Preparation ===
        _update_step(job, "pdf_repair", "active", "Checking PDF integrity...")
        job["progress"] = 5
        await asyncio.sleep(0.3)
        
        pdf_path = job["file_path"]
        filename = os.path.basename(pdf_path)
        
        # Check if file exists and is valid
        if not os.path.exists(pdf_path):
            raise FileNotFoundError(f"Upload file not found: {pdf_path}")
        
        file_size = os.path.getsize(pdf_path) / (1024 * 1024)
        _update_step(job, "pdf_repair", "done", f"PDF ready ({file_size:.1f} MB)")
        job["progress"] = 10
        
        # === Step 2: OCR Extraction ===
        _update_step(job, "ocr", "active", "Converting PDF pages to images...")
        job["progress"] = 15
        await asyncio.sleep(0.1)
        
        # Run OCR in thread pool to avoid blocking
        loop = asyncio.get_event_loop()
        ocr_result = await loop.run_in_executor(
            None, _run_ocr, pdf_path, job["dpi"], job["max_pages"], job
        )
        
        if not ocr_result or not ocr_result.get('text'):
            raise ValueError("OCR extraction produced no text")
        
        ocr_text = ocr_result['text']
        pages = ocr_result['pages']
        confidence = ocr_result['avg_confidence']
        
        _update_step(
            job, "ocr", "done",
            f"{len(ocr_text):,} chars from {pages} pages @ {confidence:.0f}% confidence"
        )
        job["progress"] = 50
        
        # Save raw OCR
        ocr_path = os.path.join(OCR_OUTPUT_DIR, f"bill_{job['id']}_{os.path.splitext(filename)[0]}.txt")
        with open(ocr_path, 'w', encoding='utf-8') as f:
            f.write(ocr_text)
        
        # === Step 3: AI Structuring ===
        _update_step(job, "llm", "active", "Extracting structured data...")
        job["progress"] = 55
        await asyncio.sleep(0.1)
        
        bill = await loop.run_in_executor(
            None, _run_extraction, ocr_text, job["use_llm"]
        )
        
        # Fill metadata
        bill.source_file = job["filename"]
        bill.total_pages = pages
        bill.ocr_confidence = confidence
        bill.extraction_timestamp = datetime.now().isoformat()
        
        method_label = "OCR + AI" if bill.extraction_method == 'OCR_LLM' else "OCR + Rules"
        fields_extracted = sum([
            1 for v in [
                bill.patient.name, bill.patient.age, bill.patient.employee_id,
                bill.hospital.name, bill.hospital.city,
                bill.admission.admission_date, bill.admission.discharge_date,
                bill.admission.diagnosis, bill.admission.treating_doctor,
                bill.bill_number, bill.bill_date,
            ] if v
        ])
        
        _update_step(
            job, "llm", "done",
            f"{method_label}: {fields_extracted} fields, {len(bill.line_items)} line items"
        )
        job["progress"] = 75
        
        # === Step 4: Rule Validation ===
        _update_step(job, "validate", "active", "Running validation rules...")
        job["progress"] = 80
        await asyncio.sleep(0.1)
        
        validation_results = await loop.run_in_executor(
            None, _run_validation, bill
        )
        
        passed = sum(1 for r in validation_results if r['status'] == 'pass')
        failed = sum(1 for r in validation_results if r['status'] == 'fail')
        warnings = sum(1 for r in validation_results if r['status'] == 'warn')
        
        _update_step(
            job, "validate", "done",
            f"{len(validation_results)} rules: {passed} passed, {failed} failed, {warnings} warnings"
        )
        job["progress"] = 95
        
        # === Assemble final result ===
        bill_id = f"bill_{job['id']}"
        bill_dict = bill.to_dict()
        bill_dict["id"] = bill_id
        bill_dict["validation_results"] = validation_results
        bill_dict["validation_summary"] = {
            "total": len(validation_results),
            "passed": passed,
            "failed": failed,
            "warnings": warnings,
        }
        
        # Save to disk
        bill_json_path = os.path.join(BILLS_JSON_DIR, f"{bill_id}.json")
        with open(bill_json_path, 'w', encoding='utf-8') as f:
            json.dump(bill_dict, f, indent=2, default=str)
        
        # Add to in-memory list
        processed_bills.append(bill_dict)
        
        # Mark job complete
        job["status"] = "completed"
        job["progress"] = 100
        job["result"] = bill_dict
        
    except Exception as e:
        job["status"] = "failed"
        job["error"] = str(e)
        tb = traceback.format_exc()
        print(f"[ERROR] Job {job_id} failed: {e}\n{tb}")
        
        # Mark current step as failed
        for step in job["steps"]:
            if step["status"] == "active":
                step["status"] = "failed"
                step["message"] = f"Error: {str(e)[:200]}"


def _update_step(job: dict, step_id: str, status: str, message: str):
    """Update or add a step in the job's progress."""
    for step in job["steps"]:
        if step["id"] == step_id:
            step["status"] = status
            step["message"] = message
            step["timestamp"] = datetime.now().isoformat()
            return
    
    job["steps"].append({
        "id": step_id,
        "status": status,
        "message": message,
        "timestamp": datetime.now().isoformat(),
    })


def _run_ocr(pdf_path: str, dpi: int, max_pages: int, job: dict) -> dict:
    """Run OCR extraction (blocking, runs in thread pool)."""
    try:
        from extract_bill import OCREngine
        
        engine = OCREngine(dpi=dpi, max_pages=max_pages)
        result = engine.extract_from_pdf(pdf_path)
        return result
    except Exception as e:
        print(f"[OCR ERROR] {e}")
        raise


def _run_extraction(ocr_text: str, use_llm: bool):
    """Run structured extraction (blocking, runs in thread pool)."""
    try:
        from extract_bill import RuleBasedExtractor, LLMExtractor, BillExtractionPipeline
        
        rule_extractor = RuleBasedExtractor()
        
        if use_llm:
            llm_extractor = LLMExtractor()
            if llm_extractor.is_available():
                bill = llm_extractor.extract(ocr_text, rule_extractor)
            else:
                print("[INFO] LLM not available, falling back to rule-based extraction")
                bill = rule_extractor.extract(ocr_text)
        else:
            bill = rule_extractor.extract(ocr_text)
        
        # Post-processing
        pipeline = BillExtractionPipeline()
        bill = pipeline._post_process(bill)
        
        return bill
    except Exception as e:
        print(f"[EXTRACTION ERROR] {e}")
        raise


def _run_validation(bill) -> List[dict]:
    """Run validation engine against extracted bill data."""
    try:
        from validation_engine import ValidationEngine
        
        engine = ValidationEngine()
        
        # Build claim dict from extracted bill
        claim = {
            "employee_id": bill.patient.employee_id or "",
            "patient_name": bill.patient.name or "",
            "patient_relationship": bill.patient.relationship or "SELF",
            "hospital_name": bill.hospital.name or "",
            "admission_date": bill.admission.admission_date or "",
            "discharge_date": bill.admission.discharge_date or "",
            "length_of_stay": bill.admission.days_stayed or 0,
            "diagnosis": bill.admission.diagnosis or "",
            "ward_type": bill.admission.ward_type or "",
            "total_claimed": bill.total_amount or 0,
            "net_amount": bill.net_amount or 0,
            "bill_number": bill.bill_number or "",
            "bill_date": bill.bill_date or "",
            "treatment_type": "IPD",
            "medical_scheme": "CPRMSE",
            "grade": "E2",
            "line_items": [],
            "has_discharge_summary": True,
            "has_referral": bool(bill.admission.referring_doctor),
            "referral_date": "",
        }
        
        # Add line items
        for item in bill.line_items:
            claim["line_items"].append({
                "item_type": item.item_type,
                "description": item.description,
                "quantity": item.quantity,
                "amount": item.amount,
            })
        
        # Run validation
        results = engine.validate_claim(claim)
        engine.close()
        
        # Convert to dicts and normalize status for frontend
        result_dicts = []
        for r in results:
            d = r.to_dict()
            # Normalize status: engine uses PASS/FAIL/WARNING, frontend uses pass/fail/warn
            status_map = {'PASS': 'pass', 'FAIL': 'fail', 'WARNING': 'warn', 'MANUAL_REVIEW': 'warn'}
            d['status'] = status_map.get(d['status'], d['status'].lower())
            result_dicts.append(d)
        return result_dicts
    except Exception as e:
        print(f"[VALIDATION ERROR] {e}")
        # Return basic validation results even if engine fails
        results = []
        
        # Basic checks we can do without the engine
        if bill.hospital.name:
            results.append({
                "rule_id": "E003",
                "status": "warn",
                "severity": "WARNING",
                "message": f"Hospital '{bill.hospital.name}' — empanelment check requires database lookup",
                "details": {},
                "amount_impact": 0,
            })
        
        if bill.total_amount and bill.total_amount >= 500000:
            results.append({
                "rule_id": "HV001",
                "status": "warn",
                "severity": "WARNING", 
                "message": f"Bill ₹{bill.total_amount:,.0f} exceeds ₹5 lakh — requires multi-doctor scrutiny",
                "details": {},
                "amount_impact": 0,
            })
        
        if bill.admission.days_stayed and bill.admission.days_stayed > 15:
            results.append({
                "rule_id": "HV002",
                "status": "fail",
                "severity": "ERROR",
                "message": f"Stay of {bill.admission.days_stayed} days exceeds 15-day limit — CMS approval required",
                "details": {},
                "amount_impact": 0,
            })
        
        return results


# ============================================================================
# Entry Point
# ============================================================================

if __name__ == "__main__":
    import uvicorn
    
    print("=" * 60)
    print("  MedBill AI — Backend Server")
    print("=" * 60)
    print(f"\n  Dashboard:  http://localhost:8080")
    print(f"  API Docs:   http://localhost:8080/docs")
    print(f"  Database:   {DB_PATH}")
    print(f"  Uploads:    {UPLOADS_DIR}")
    print(f"  Bills:      {len(processed_bills)} previously processed")
    print("=" * 60 + "\n")
    
    uvicorn.run(app, host="0.0.0.0", port=8080, log_level="info")
