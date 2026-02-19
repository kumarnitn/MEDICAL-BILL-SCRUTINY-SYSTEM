#!/usr/bin/env python3
"""
Validation Rule Engine
========================
Loads validation rules from YAML and provides programmatic rule checking
against medical claims data.

This is the deterministic rule engine component of the hybrid architecture
(VLM extracts data → Rule Engine validates → LLM adjudicates edge cases).
"""

import yaml
import sqlite3
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any


RULES_FILE = os.path.join(os.path.dirname(__file__), '..', 'data', 'validation_rules.yaml')
DB_PATH = os.path.join(os.path.dirname(__file__), '..', 'data', 'processed', 'medical_bills.db')


class ValidationResult:
    """Result of a single rule check."""
    
    def __init__(self, rule_id: str, status: str, severity: str, 
                 message: str, details: Optional[Dict] = None,
                 amount_impact: float = 0):
        self.rule_id = rule_id
        self.status = status      # PASS, FAIL, WARNING, MANUAL_REVIEW
        self.severity = severity   # ERROR, WARNING, INFO
        self.message = message
        self.details = details or {}
        self.amount_impact = amount_impact
    
    def __repr__(self):
        icon = {'PASS': '✅', 'FAIL': '❌', 'WARNING': '⚠️', 'MANUAL_REVIEW': '🔍'}.get(self.status, '❓')
        return f"{icon} [{self.rule_id}] {self.message}"
    
    def to_dict(self):
        return {
            'rule_id': self.rule_id,
            'status': self.status,
            'severity': self.severity,
            'message': self.message,
            'details': self.details,
            'amount_impact': self.amount_impact,
        }


class ValidationEngine:
    """Rule engine for medical bill validation."""
    
    def __init__(self, rules_path: str = None, db_path: str = None):
        self.rules_path = rules_path or os.path.abspath(RULES_FILE)
        self.db_path = db_path or os.path.abspath(DB_PATH)
        self.rules = self._load_rules()
        self._conn = None
    
    def _load_rules(self) -> Dict:
        """Load validation rules from YAML."""
        with open(self.rules_path, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f)
    
    @property
    def conn(self):
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path)
            self._conn.row_factory = sqlite3.Row
        return self._conn
    
    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None
    
    # ----------------------------------------------------------------
    # Rule Lookups
    # ----------------------------------------------------------------
    
    def get_room_entitlement(self, grade: str, scheme: str = 'MAR') -> str:
        """Get room entitlement for a grade and scheme."""
        entitlements = None
        for rule in self.rules.get('room_rent', []):
            if rule['rule_id'] == 'RR001':
                entitlements = rule.get('entitlements', {})
                break
        
        if not entitlements:
            return 'UNKNOWN'
        
        scheme_entitlements = entitlements.get(scheme, entitlements.get('MAR', {}))
        return scheme_entitlements.get(grade, 'Twin Sharing (AC)')
    
    def get_spectacles_ceiling(self, grade: str) -> float:
        """Get spectacles reimbursement ceiling for a grade."""
        for rule in self.rules.get('spectacles', []):
            if rule['rule_id'] == 'SP001':
                return rule.get('ceilings', {}).get(grade, 10000)
        return 10000
    
    def get_scrutiny_requirements(self, amount: float) -> Dict:
        """Get multi-doctor scrutiny requirements for a bill amount."""
        for rule in self.rules.get('high_value', []):
            if rule['rule_id'] == 'HV001':
                thresholds = rule.get('thresholds', [])
                for t in sorted(thresholds, key=lambda x: x['amount'], reverse=True):
                    if amount >= t['amount']:
                        return {'min_scrutinizers': t['min_scrutinizers'], 'label': t['label']}
        return {'min_scrutinizers': 1, 'label': '1 doctor'}
    
    # ----------------------------------------------------------------
    # CGHS Rate Matching
    # ----------------------------------------------------------------
    
    def find_cghs_rate(self, procedure_name: str, nabh: bool = False) -> Optional[Dict]:
        """Find CGHS rate for a procedure using FTS search."""
        c = self.conn.cursor()
        
        # Try exact match first
        rows = c.execute(
            "SELECT r.* FROM cghs_rates r WHERE LOWER(r.procedure_name) = LOWER(?)",
            (procedure_name,)
        ).fetchall()
        
        if not rows:
            # FTS search
            # Clean query for FTS
            clean_query = procedure_name.replace('-', ' ').replace('/', ' ')
            try:
                rows = c.execute(
                    """SELECT r.* FROM cghs_rates r 
                       JOIN cghs_rates_fts f ON f.rowid = r.id 
                       WHERE f.procedure_name MATCH ? 
                       LIMIT 5""",
                    (clean_query,)
                ).fetchall()
            except Exception:
                rows = []
        
        if rows:
            row = rows[0]
            rate = row['nabh_rate'] if nabh else row['non_nabh_rate']
            return {
                'id': row['id'],
                'procedure_name': row['procedure_name'],
                'category': row['category'],
                'rate': rate,
                'non_nabh_rate': row['non_nabh_rate'],
                'nabh_rate': row['nabh_rate'],
            }
        return None
    
    # ----------------------------------------------------------------
    # Hospital Verification
    # ----------------------------------------------------------------
    
    def verify_hospital(self, hospital_name: str) -> Optional[Dict]:
        """Check if a hospital is empanelled."""
        c = self.conn.cursor()
        
        # Try LIKE search
        rows = c.execute(
            "SELECT * FROM hospitals WHERE hospital_name LIKE ? LIMIT 3",
            (f'%{hospital_name}%',)
        ).fetchall()
        
        if not rows:
            # Try FTS
            try:
                rows = c.execute(
                    "SELECT h.* FROM hospitals h JOIN hospitals_fts f ON f.rowid = h.id WHERE f.hospital_name MATCH ? LIMIT 3",
                    (hospital_name,)
                ).fetchall()
            except Exception:
                rows = []
        
        if rows:
            row = rows[0]
            return {
                'id': row['id'],
                'hospital_name': row['hospital_name'],
                'city': row['city'],
                'empanelled_for': row['empanelled_for'],
                'empanelment_date': row['empanelment_date'],
            }
        return None
    
    # ----------------------------------------------------------------
    # Claim Validation Methods
    # ----------------------------------------------------------------
    
    def validate_claim(self, claim: Dict) -> List[ValidationResult]:
        """
        Run all applicable validation rules against a claim.
        
        Args:
            claim: Dict with keys like:
                - employee_id, patient_name, patient_relationship
                - hospital_name, is_empanelled
                - treatment_type (OPD/IPD/DAYCARE/DOMICILIARY)
                - admission_date, discharge_date
                - claimed_amount
                - line_items: list of dicts with item_type, description, amount
                - referral_date (optional)
                - medical_scheme (MAR/CPRMSE/CPRMSNE)
                - grade (E1-E9, BOARD, NON_EXE)
        
        Returns:
            List of ValidationResult objects
        """
        results = []
        
        # Eligibility checks
        results.extend(self._check_hospital_empanelment(claim))
        results.extend(self._check_referral_validity(claim))
        
        # Rate validation
        results.extend(self._check_cghs_rates(claim))
        
        # Room rent
        results.extend(self._check_room_entitlement(claim))
        results.extend(self._check_bed_days(claim))
        
        # Package rules
        results.extend(self._check_package_rules(claim))
        
        # High value rules
        results.extend(self._check_high_value_rules(claim))
        
        # Extended stay
        results.extend(self._check_extended_stay(claim))
        
        # OPD limits
        results.extend(self._check_opd_limits(claim))
        
        # Documentation
        results.extend(self._check_documentation(claim))
        
        return results
    
    def _check_hospital_empanelment(self, claim: Dict) -> List[ValidationResult]:
        """Rule E003: Hospital empanelment check."""
        results = []
        hospital_name = claim.get('hospital_name', '')
        
        if not hospital_name:
            results.append(ValidationResult(
                'E003', 'FAIL', 'ERROR',
                'Hospital name is missing from claim'
            ))
            return results
        
        hospital = self.verify_hospital(hospital_name)
        if hospital:
            results.append(ValidationResult(
                'E003', 'PASS', 'INFO',
                f"Hospital '{hospital['hospital_name']}' is empanelled in {hospital['city']}",
                details=hospital
            ))
        else:
            results.append(ValidationResult(
                'E003', 'FAIL', 'ERROR',
                f"Hospital '{hospital_name}' NOT found in empanelled list",
                details={'searched': hospital_name}
            ))
        
        return results
    
    def _check_referral_validity(self, claim: Dict) -> List[ValidationResult]:
        """Rule E004: Referral validity (45 days)."""
        results = []
        referral_date = claim.get('referral_date')
        admission_date = claim.get('admission_date')
        
        if not referral_date or not admission_date:
            return results
        
        try:
            ref_dt = datetime.strptime(referral_date, '%Y-%m-%d')
            adm_dt = datetime.strptime(admission_date, '%Y-%m-%d')
            gap = (adm_dt - ref_dt).days
            
            if gap > 45:
                results.append(ValidationResult(
                    'E004', 'FAIL', 'ERROR',
                    f"Admission is {gap} days after referral (max 45 days)",
                    details={'referral_date': referral_date, 'admission_date': admission_date, 'gap_days': gap},
                    amount_impact=claim.get('claimed_amount', 0)
                ))
            elif gap < 0:
                results.append(ValidationResult(
                    'E004', 'FAIL', 'ERROR',
                    f"Admission date ({admission_date}) is BEFORE referral date ({referral_date})",
                    details={'referral_date': referral_date, 'admission_date': admission_date}
                ))
            else:
                results.append(ValidationResult(
                    'E004', 'PASS', 'INFO',
                    f"Referral valid: admission {gap} days after referral (within 45-day limit)"
                ))
        except ValueError:
            pass
        
        return results
    
    def _check_cghs_rates(self, claim: Dict) -> List[ValidationResult]:
        """Rule R001: CGHS rate cap for line items."""
        results = []
        line_items = claim.get('line_items', [])
        nabh = claim.get('nabh', False)
        
        for item in line_items:
            if item.get('item_type') in ('PROCEDURE', 'PACKAGE', 'INVESTIGATION', 'CONSULTATION'):
                description = item.get('description', '')
                amount = item.get('amount', 0)
                
                cghs = self.find_cghs_rate(description, nabh)
                if cghs and cghs['rate']:
                    if amount > cghs['rate']:
                        excess = amount - cghs['rate']
                        results.append(ValidationResult(
                            'R001', 'FAIL', 'ERROR',
                            f"'{description}' charged ₹{amount:,.0f} exceeds CGHS rate ₹{cghs['rate']:,.0f} (excess ₹{excess:,.0f})",
                            details={'item': description, 'charged': amount, 'cghs_rate': cghs['rate'],
                                     'cghs_procedure': cghs['procedure_name']},
                            amount_impact=excess
                        ))
                    else:
                        results.append(ValidationResult(
                            'R001', 'PASS', 'INFO',
                            f"'{description}' ₹{amount:,.0f} within CGHS rate ₹{cghs['rate']:,.0f}",
                            details={'item': description, 'cghs_procedure': cghs['procedure_name']}
                        ))
                elif description:
                    results.append(ValidationResult(
                        'R002', 'WARNING', 'WARNING',
                        f"No CGHS rate found for '{description}' — check AIIMS rate or actual",
                        details={'item': description, 'amount': amount}
                    ))
        
        return results
    
    def _check_room_entitlement(self, claim: Dict) -> List[ValidationResult]:
        """Rule RR001: Room entitlement by grade."""
        results = []
        grade = claim.get('grade')
        scheme = claim.get('medical_scheme', 'MAR')
        
        if grade:
            entitlement = self.get_room_entitlement(grade, scheme)
            results.append(ValidationResult(
                'RR001', 'PASS', 'INFO',
                f"Grade {grade} ({scheme}) entitled to: {entitlement}",
                details={'grade': grade, 'scheme': scheme, 'entitlement': entitlement}
            ))
        
        return results
    
    def _check_bed_days(self, claim: Dict) -> List[ValidationResult]:
        """Rule RR003: Bed charge day counting."""
        results = []
        admission_date = claim.get('admission_date')
        discharge_date = claim.get('discharge_date')
        
        if admission_date and discharge_date:
            try:
                adm = datetime.strptime(admission_date, '%Y-%m-%d')
                dis = datetime.strptime(discharge_date, '%Y-%m-%d')
                expected_days = (dis - adm).days
                
                billed_days = claim.get('billed_bed_days')
                if billed_days and billed_days > expected_days:
                    results.append(ValidationResult(
                        'RR003', 'FAIL', 'WARNING',
                        f"Billed {billed_days} bed days but expected {expected_days} (admission to discharge diff)",
                        details={'billed_days': billed_days, 'expected_days': expected_days},
                        amount_impact=0  # Would need room rate to calculate
                    ))
            except ValueError:
                pass
        
        return results
    
    def _check_package_rules(self, claim: Dict) -> List[ValidationResult]:
        """Rules P001-P005: Package-related checks."""
        results = []
        line_items = claim.get('line_items', [])
        
        has_package = any(item.get('item_type') == 'PACKAGE' for item in line_items)
        
        if has_package:
            # Check for items that should be included in package
            excluded_types = {'ROOM_RENT', 'CONSULTATION', 'OT_CHARGES', 'DRESSING',
                            'INVESTIGATION', 'MEDICINE', 'CONSUMABLE', 'NURSING'}
            
            separate_charges = [item for item in line_items 
                              if item.get('item_type') in excluded_types 
                              and item.get('amount', 0) > 0
                              and not item.get('is_pre_package')]  # Items before package start are OK
            
            if separate_charges:
                total_excess = sum(item.get('amount', 0) for item in separate_charges)
                items_list = ', '.join(f"{item['item_type']}=₹{item.get('amount', 0):,.0f}" 
                                      for item in separate_charges[:5])
                results.append(ValidationResult(
                    'P001', 'FAIL', 'ERROR',
                    f"Package claimed but separate charges found: {items_list}. Total excess: ₹{total_excess:,.0f}",
                    details={'separate_items': [i['item_type'] for i in separate_charges]},
                    amount_impact=total_excess
                ))
            
            # Check for multiple surgeries (P005)
            surgeries = [item for item in line_items 
                        if item.get('item_type') in ('PROCEDURE', 'PACKAGE')]
            if len(surgeries) > 1:
                results.append(ValidationResult(
                    'P005', 'WARNING', 'WARNING',
                    f"Multiple surgeries/packages ({len(surgeries)}) in same claim. "
                    f"Second surgery should be at 50% rate.",
                    details={'surgery_count': len(surgeries)}
                ))
        
        return results
    
    def _check_high_value_rules(self, claim: Dict) -> List[ValidationResult]:
        """Rule HV001: Multi-doctor scrutiny."""
        results = []
        amount = claim.get('claimed_amount', 0)
        
        req = self.get_scrutiny_requirements(amount)
        if req['min_scrutinizers'] > 1:
            results.append(ValidationResult(
                'HV001', 'WARNING', 'WARNING',
                f"Bill ₹{amount:,.0f} requires scrutiny by {req['label']}",
                details={'amount': amount, 'required': req}
            ))
        
        return results
    
    def _check_extended_stay(self, claim: Dict) -> List[ValidationResult]:
        """Rule HV002: CMS approval for >15 days stay."""
        results = []
        admission_date = claim.get('admission_date')
        discharge_date = claim.get('discharge_date')
        
        if admission_date and discharge_date:
            try:
                adm = datetime.strptime(admission_date, '%Y-%m-%d')
                dis = datetime.strptime(discharge_date, '%Y-%m-%d')
                stay = (dis - adm).days
                
                if stay > 15:
                    cms_attached = claim.get('cms_approval_attached', False)
                    if not cms_attached:
                        results.append(ValidationResult(
                            'HV002', 'FAIL', 'ERROR',
                            f"Stay of {stay} days exceeds 15 days. CMS approval required but not attached.",
                            details={'stay_days': stay}
                        ))
                    else:
                        results.append(ValidationResult(
                            'HV002', 'PASS', 'INFO',
                            f"Stay of {stay} days with CMS approval attached."
                        ))
            except ValueError:
                pass
        
        return results
    
    def _check_opd_limits(self, claim: Dict) -> List[ValidationResult]:
        """Rule OPD001: CPRMS-NE OPD annual limit."""
        results = []
        scheme = claim.get('medical_scheme')
        treatment_type = claim.get('treatment_type')
        
        if scheme == 'CPRMSNE' and treatment_type in ('OPD', 'DOMICILIARY'):
            amount = claim.get('claimed_amount', 0)
            prior_claims = claim.get('prior_opd_claims_this_fy', 0)
            total = prior_claims + amount
            
            if total > 25000:
                excess = total - 25000
                results.append(ValidationResult(
                    'OPD001', 'FAIL', 'ERROR',
                    f"CPRMS-NE OPD total ₹{total:,.0f} exceeds ₹25,000 annual limit per card. Excess: ₹{excess:,.0f}",
                    details={'current_claim': amount, 'prior_claims': prior_claims, 'total': total},
                    amount_impact=excess
                ))
            else:
                remaining = 25000 - total
                results.append(ValidationResult(
                    'OPD001', 'PASS', 'INFO',
                    f"CPRMS-NE OPD: ₹{total:,.0f} of ₹25,000 used. Remaining: ₹{remaining:,.0f}"
                ))
        
        return results
    
    def _check_documentation(self, claim: Dict) -> List[ValidationResult]:
        """Rules D001-D005: Documentation checks."""
        results = []
        
        # D005: Discharge summary for IPD
        if claim.get('treatment_type') == 'IPD':
            if not claim.get('has_discharge_summary', True):
                results.append(ValidationResult(
                    'D005', 'FAIL', 'ERROR',
                    'Discharge summary not found for IPD claim'
                ))
        
        # D002: Blood transfusion docs
        line_items = claim.get('line_items', [])
        has_blood = any(item.get('item_type') == 'BLOOD_TRANSFUSION' for item in line_items)
        if has_blood and not claim.get('blood_transfusion_documented', True):
            results.append(ValidationResult(
                'D002', 'FAIL', 'ERROR',
                'Blood transfusion charged but no documentary evidence or discharge summary mention'
            ))
        
        return results
    
    # ----------------------------------------------------------------
    # Summary
    # ----------------------------------------------------------------
    
    def generate_summary(self, results: List[ValidationResult]) -> Dict:
        """Generate a summary of validation results."""
        total = len(results)
        passed = sum(1 for r in results if r.status == 'PASS')
        failed = sum(1 for r in results if r.status == 'FAIL')
        warnings = sum(1 for r in results if r.status == 'WARNING')
        total_impact = sum(r.amount_impact for r in results if r.status == 'FAIL')
        
        return {
            'total_rules_checked': total,
            'passed': passed,
            'failed': failed,
            'warnings': warnings,
            'total_amount_impact': total_impact,
            'overall_status': 'REJECTED' if failed > 0 else ('REVIEW' if warnings > 0 else 'APPROVED'),
        }


def demo_validation():
    """Demonstrate the validation engine with a sample claim."""
    engine = ValidationEngine()
    
    print("=" * 70)
    print("  Medical Bill Validation Engine - Demo")
    print("=" * 70)
    
    # Load rule count
    rule_count = sum(len(v) if isinstance(v, list) else 0 
                     for v in engine.rules.values() if isinstance(v, (list, dict)))
    print(f"\n  Rules loaded: {rule_count} rules from {engine.rules_path}")
    
    # Demo claim
    sample_claim = {
        'employee_id': 'SECL-12345',
        'patient_name': 'Ram Kumar',
        'patient_relationship': 'SELF',
        'hospital_name': 'Apollo Hospitals',
        'is_empanelled': True,
        'nabh': True,
        'treatment_type': 'IPD',
        'admission_date': '2025-01-10',
        'discharge_date': '2025-01-15',
        'claimed_amount': 250000,
        'medical_scheme': 'MAR',
        'grade': 'E6',
        'referral_date': '2025-01-05',
        'has_discharge_summary': True,
        'billed_bed_days': 5,
        'line_items': [
            {'item_type': 'CONSULTATION', 'description': 'Consultation OPD', 'amount': 500},
            {'item_type': 'PACKAGE', 'description': 'Appendicectomy', 'amount': 25000},
            {'item_type': 'ROOM_RENT', 'description': 'Private Room AC', 'amount': 15000},
            {'item_type': 'MEDICINE', 'description': 'Pharmacy charges', 'amount': 8000},
            {'item_type': 'IMPLANT', 'description': 'Surgical implant', 'amount': 50000},
            {'item_type': 'INVESTIGATION', 'description': 'CT Scan Abdomen', 'amount': 5000},
        ]
    }
    
    print(f"\n  Sample Claim:")
    print(f"    Patient: {sample_claim['patient_name']}")
    print(f"    Hospital: {sample_claim['hospital_name']}")
    print(f"    Treatment: {sample_claim['treatment_type']}")
    print(f"    Amount: ₹{sample_claim['claimed_amount']:,.0f}")
    print(f"    Grade: {sample_claim['grade']} ({sample_claim['medical_scheme']})")
    print(f"    Stay: {sample_claim['admission_date']} to {sample_claim['discharge_date']}")
    
    # Run validation
    print(f"\n  Running validation...")
    print(f"  {'—' * 60}")
    
    results = engine.validate_claim(sample_claim)
    
    for r in results:
        print(f"  {r}")
    
    # Summary
    summary = engine.generate_summary(results)
    print(f"\n  {'=' * 60}")
    print(f"  VALIDATION SUMMARY")
    print(f"  {'=' * 60}")
    print(f"  Rules checked: {summary['total_rules_checked']}")
    print(f"  ✅ Passed:    {summary['passed']}")
    print(f"  ❌ Failed:    {summary['failed']}")
    print(f"  ⚠️  Warnings:  {summary['warnings']}")
    print(f"  💰 Amount impact: ₹{summary['total_amount_impact']:,.0f}")
    print(f"  📋 Status: {summary['overall_status']}")
    
    engine.close()
    
    print(f"\n{'=' * 70}")
    print(f"  Done!")
    print(f"{'=' * 70}")


if __name__ == '__main__':
    demo_validation()
