#!/usr/bin/env python3
"""
Hospital Empanelment List Parser v5
====================================
Final robust approach: process the layout file line by line,
identifying hospital entries by looking for the complete pattern.

Key insight from layout analysis:
- Lines with serial numbers (1., 2., etc.) at col 0-5
- City names at col 6-22 (on same or next line as sl_no)
- Hospital text at col 18-65 (name starts ABOVE sl_no line, runs through)
- Empanelment text at col 60-95
- Dates at col 90+

Strategy:
1. Find serial number positions
2. For each entry N, the hospital text block is from end of entry N-1's
   empanelment section to entry N+1's hospital text start
3. Use heuristics to identify the hospital NAME vs address within the text block
"""

import re
import csv
import sqlite3
import os
import subprocess

RAW_LAYOUT_FILE = os.path.join(os.path.dirname(__file__), '..', 'data', 'raw', 'hospital_list_layout.txt')
OUTPUT_CSV = os.path.join(os.path.dirname(__file__), '..', 'data', 'processed', 'hospitals.csv')
OUTPUT_DB = os.path.join(os.path.dirname(__file__), '..', 'data', 'processed', 'medical_bills.db')
PDF_FILE = os.path.join(os.path.dirname(__file__), '..', 'Hospital List 08.10.2025 (1).pdf')

KNOWN_CITIES = set("""
ahmedabad allahabad prayagraj ajmer ambikapur angamaly asansol bangalore bengaluru
barasat bargarh bhilai bhopal bhubaneshwar bhubaneswar bilaspur bokaro burdwan
bardhaman calicut kozhikode chandigarh chandrapur chennai coimbatore cuttack
dankuni dehradun delhi dhanbad dhule durg durgapur dibrugarh ernakulam faridabad
ghaziabad goa gorakhpur gurgaon gurugram guwahati hyderabad indore jabalpur
jaipur jamshedpur jodhpur kanpur kochi kolkata korba lucknow ludhiana madurai
mangalore mohali mumbai muzaffarpur mysore mysuru nagpur nashik noida panaji
patna puducherry pune raigarh raipur rajkot ranchi rourkela sambalpur 
secunderabad siliguri surat thane thiruvananthapuram tiruchirappalli trichy
trivandrum udaipur vadodara varanasi vijayawada visakhapatnam vizag vellore
hazaribagh sindri ramgarh sasaram howrah kalyani medinipur purulia bankura
balasore berhampur jharsuguda sundargarh jajpur angul dhenkanal
""".split())


def extract_with_layout():
    result = subprocess.run(
        ['pdftotext', '-layout', PDF_FILE, RAW_LAYOUT_FILE],
        capture_output=True, text=True
    )
    return result.returncode == 0


def find_city(text):
    """Find a known city name in text."""
    text_lower = text.lower().strip()
    # Direct match
    if text_lower in KNOWN_CITIES:
        return text_lower.title()
    # Handle multi-word cities
    for mc in ['new delhi', 'navi mumbai', 'greater noida']:
        if mc in text_lower:
            return mc.title()
    # Partial match at start
    for city in KNOWN_CITIES:
        if text_lower.startswith(city):
            return city.title()
    return ''


def is_hospital_name_like(text):
    """Heuristic: does this text look like a hospital name?"""
    t = text.strip()
    if not t:
        return False
    # Hospital name indicators
    indicators = [
        r'\bhospital\b', r'\bclinic\b', r'\bmedical\b', r'\binstitut\b',
        r'\bcentre\b', r'\bcenter\b', r'\bnursing\b', r'\bdiagnostic\b',
        r'\bhealthcare\b', r'\bhealth\s*care\b', r'\bhealth\s*world\b',
        r'\bspecialit\b', r'\bmission\b', r'\bfoundation\b', r'\btrust\b',
        r'\bapollo\b', r'\bfortis\b', r'\bnarayana\b', r'\bmax\b',
        r'\bsagar\b', r'\baster\b', r'\bcare\b', r'\baiims\b',
        r'\bnetralaya\b', r'\bnethralaya\b', r'\beye\b',
        r'\bLtd\b', r'\bPvt\b', r'\bPrivate\b', r'\bLimited\b',
        r'\bResearch\b', r'\bMemorial\b', r'\bCharitable\b',
    ]
    for ind in indicators:
        if re.search(ind, t, re.IGNORECASE):
            return True
    # All caps text >15 chars is likely a hospital name
    if t.isupper() and len(t) > 15:
        return True
    return False


def parse_layout_file(filepath):
    """Parse the layout-preserved text file."""
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    content = content.replace('\f', '\n')
    lines = content.split('\n')
    
    # Skip header (find "W.E.F" line)
    data_start = 0
    for i, line in enumerate(lines):
        if 'W.E.F' in line:
            data_start = i + 1
            break
    
    # Find all serial number positions
    sl_positions = []
    for i in range(data_start, len(lines)):
        m = re.match(r'^\s{0,5}(\d{1,3})\.\s', lines[i])
        if m:
            sl_positions.append((i, int(m.group(1))))
    
    print(f"  Found {len(sl_positions)} serial markers")
    
    entries = []
    
    for idx, (sl_line_idx, sl_no) in enumerate(sl_positions):
        # Define search boundaries
        # Look up from sl_no for hospital name (up to 6 lines above)
        # Look down from sl_no for city, more address, empanelment
        
        # Upper bound: 2 lines after previous sl_no (to avoid taking previous entry's data)
        if idx > 0:
            upper_bound = sl_positions[idx - 1][0] + 2
        else:
            upper_bound = data_start
        
        # Lower bound: next sl_no line
        if idx + 1 < len(sl_positions):
            lower_bound = sl_positions[idx + 1][0]
        else:
            lower_bound = min(len(lines), sl_line_idx + 20)
        
        # ---- Extract data from the block ----
        city = ''
        hospital_name = ''
        address_parts = []
        emp_parts = []
        date_parts = []
        phone_parts = []
        email_parts = []
        payment = ''
        
        # Scan ALL lines in the block (from upper_bound to lower_bound)
        # For each line, extract what's in the hospital column (col ~18-58)
        # and what's in the empanelment column (col ~58+)
        
        hospital_col_texts = []  # (line_idx, text) from column 18-58
        
        for i in range(max(upper_bound, sl_line_idx - 8), lower_bound):
            if i >= len(lines):
                break
            raw = lines[i]
            stripped = raw.strip()
            
            # Skip empty and header lines
            if not stripped:
                continue
            if re.search(r'(Coal India|MAHARATNA|Medical Division|Coal Bhawan|EMPANELLED HOSPITALS|Updated on|In supersession|All empaneled|hospitals own rates|^Sl\.\s|CITY\s+NAME|EMPANELMENT\s*$|^W\.E\.F\s*$|^\s*No\s*$)', stripped, re.IGNORECASE):
                continue
            
            # City: look for city name in col 6-22
            if not city:
                city_text = raw[6:22].strip() if len(raw) > 6 else ''
                found = find_city(city_text)
                if found:
                    city = found
            
            # Hospital/address column (18-58)
            hosp_text = raw[18:58].strip() if len(raw) > 18 else ''
            if hosp_text:
                # Filter out empanelment text that bleeds into this column
                if not re.search(r'(Total\s+facilit|Accepted|Acceptable|Single\s+[Ss]pecial|Payment|OPD\s*[&]\s*Indoor|in\s+favo)', hosp_text, re.IGNORECASE):
                    hospital_col_texts.append((i, hosp_text))
            
            # Empanelment column (58+)
            emp_text = raw[58:].strip() if len(raw) > 58 else ''
            if emp_text:
                # Extract dates
                dates = re.findall(r'\d{2}\.\d{2}\.\d{2,4}', emp_text)
                date_parts.extend(dates)
                
                # Clean dates from text
                emp_clean = re.sub(r'\d{2}\.\d{2}\.\d{2,4}', '', emp_text).strip()
                if emp_clean:
                    emp_parts.append(emp_clean)
            
            # Email and phone from full line
            emails = re.findall(r'[\w.+-]+@[\w.-]+\.\w+', stripped)
            email_parts.extend(emails)
            
            phones = re.findall(r'(?:Ph|Tel|PH|Fax)\s*[.:]*\s*[-\s]*([\d\s/\-+,()]+)', stripped, re.IGNORECASE)
            phone_parts.extend([p.strip() for p in phones if len(p.strip()) > 3])
            
            # Payment notes
            if re.search(r'(Payment|in\s+favo)', stripped, re.IGNORECASE):
                payment += ' ' + stripped
        
        # Determine hospital name from hospital_col_texts
        # First text that looks like a hospital name
        for li, txt in hospital_col_texts:
            if is_hospital_name_like(txt):
                hospital_name = txt
                # Remaining texts are address
                for li2, txt2 in hospital_col_texts:
                    if li2 != li and txt2 != txt:
                        address_parts.append(txt2)
                break
        
        # If no hospital-like name found, use first text as name
        if not hospital_name and hospital_col_texts:
            hospital_name = hospital_col_texts[0][1]
            for li2, txt2 in hospital_col_texts[1:]:
                address_parts.append(txt2)
        
        # Clean up
        hospital_name = hospital_name.strip().rstrip(',')
        address = ', '.join([p.strip() for p in address_parts if p.strip()]).strip().rstrip(',')
        emp_text = ' '.join(emp_parts).strip()
        emp_text = re.sub(r'\s+', ' ', emp_text)
        emp_date = date_parts[0] if date_parts else ''
        
        if hospital_name:
            entries.append({
                'sl_no': sl_no,
                'city': city,
                'hospital_name': hospital_name,
                'address': address,
                'phone': '; '.join(phone_parts) if phone_parts else '',
                'email': '; '.join(set(email_parts)) if email_parts else '',
                'empanelled_for': emp_text,
                'empanelment_date': emp_date,
                'payment_notes': payment.strip(),
            })
    
    return entries


def save_to_csv(entries, output_path):
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    fields = ['sl_no', 'city', 'hospital_name', 'address', 'phone', 'email',
              'empanelled_for', 'empanelment_date', 'payment_notes']
    with open(output_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(entries)
    print(f"\n✅ CSV: {len(entries)} hospitals → {output_path}")


def save_to_sqlite(entries, db_path):
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    
    c.execute('DROP TABLE IF EXISTS hospitals_fts')
    c.execute('DROP TABLE IF EXISTS hospitals')
    
    c.execute('''CREATE TABLE hospitals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        sl_no INTEGER,
        city TEXT,
        hospital_name TEXT NOT NULL,
        address TEXT,
        phone TEXT,
        email TEXT,
        empanelled_for TEXT,
        empanelment_date TEXT,
        payment_notes TEXT,
        nabh_status TEXT DEFAULT 'UNKNOWN',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    
    c.execute('''CREATE VIRTUAL TABLE hospitals_fts USING fts5(
        hospital_name, city, empanelled_for,
        content='hospitals', content_rowid='id'
    )''')
    
    for e in entries:
        c.execute('''INSERT INTO hospitals 
                     (sl_no, city, hospital_name, address, phone, email,
                      empanelled_for, empanelment_date, payment_notes)
                     VALUES (?,?,?,?,?,?,?,?,?)''',
                  (e['sl_no'], e['city'], e['hospital_name'], e['address'],
                   e['phone'], e['email'], e['empanelled_for'],
                   e['empanelment_date'], e['payment_notes']))
    
    c.execute('''INSERT INTO hospitals_fts (rowid, hospital_name, city, empanelled_for)
                 SELECT id, hospital_name, city, empanelled_for FROM hospitals''')
    conn.commit()
    
    # --- Report ---
    total = c.execute('SELECT COUNT(*) FROM hospitals').fetchone()[0]
    with_city = c.execute("SELECT COUNT(*) FROM hospitals WHERE city != ''").fetchone()[0]
    with_date = c.execute("SELECT COUNT(*) FROM hospitals WHERE empanelment_date != ''").fetchone()[0]
    
    print(f"\n✅ SQLite: {total} hospitals → {db_path}")
    print(f"   With city: {with_city}/{total} ({100*with_city//max(total,1)}%)")
    print(f"   With date: {with_date}/{total} ({100*with_date//max(total,1)}%)")
    
    cities = c.execute("""SELECT city, COUNT(*) FROM hospitals 
                         WHERE city != '' GROUP BY UPPER(city) ORDER BY COUNT(*) DESC LIMIT 15""").fetchall()
    print(f"\n   Top 15 cities:")
    for ct, cnt in cities:
        print(f"     {cnt:3d} | {ct}")
    
    total_cities = c.execute("SELECT COUNT(DISTINCT UPPER(city)) FROM hospitals WHERE city != ''").fetchone()[0]
    print(f"   Total unique cities: {total_cities}")
    
    print(f"\n   First 10:")
    for r in c.execute('SELECT sl_no, city, hospital_name, empanelment_date FROM hospitals ORDER BY id LIMIT 10'):
        print(f"     #{r[0]:3d} | {(r[1] or ''):15s} | {r[2][:45]:45s} | {r[3] or ''}")
    
    print(f"\n   Verification:")
    tests = [
        ('SAMVED', 'Ahmedabad'),
        ('CIMS Hospital', 'Ahmedabad'),
        ('Apollo Hospital', None),
        ('FORTIS', None),
        ('JEEVAN JYOTI', None),
        ('Narayana', None),
        ('Chirayu', 'Bhopal'),
    ]
    for query, exp_city in tests:
        rows = c.execute("SELECT sl_no, hospital_name, city FROM hospitals WHERE hospital_name LIKE ? LIMIT 3", 
                         (f'%{query}%',)).fetchall()
        if rows:
            for sl, name, ct in rows:
                status = "✅"
                print(f"     {status} #{sl:3d} {name[:42]:42s} → {ct}")
        else:
            # Try FTS
            fts_rows = c.execute("SELECT hospital_name, city FROM hospitals_fts WHERE hospital_name MATCH ? LIMIT 3",
                                (query,)).fetchall()
            if fts_rows:
                for name, ct in fts_rows:
                    print(f"     ✅ (FTS) {name[:42]:42s} → {ct}")
            else:
                print(f"     ❌ '{query}' not found!")
    
    conn.close()


if __name__ == '__main__':
    print("=" * 70)
    print("  Hospital Empanelment List Parser v5")
    print("=" * 70)
    
    if not os.path.exists(RAW_LAYOUT_FILE) and os.path.exists(PDF_FILE):
        extract_with_layout()
    
    entries = parse_layout_file(os.path.abspath(RAW_LAYOUT_FILE))
    print(f"  Parsed {len(entries)} hospitals")
    
    save_to_csv(entries, os.path.abspath(OUTPUT_CSV))
    save_to_sqlite(entries, os.path.abspath(OUTPUT_DB))
    
    print("\n" + "=" * 70)
    print("  Done!")
    print("=" * 70)
