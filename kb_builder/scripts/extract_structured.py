#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
extract_structured.py — Extract structured data from Excel, 知网 exports, ClinicalTrials JSON, etc.

Unified interface for parsing non-document structured sources into a normalized format
that the KB builder can consume.

Usage:
    python3 extract_structured.py input.xlsx [--type excel|cnki|pubmed|clinicaltrials]

If --type is omitted, auto-detects based on file extension and content.

Output: JSON with normalized structure:
{
  "source_type": "excel|cnki_refworks|pubmed_xml|clinicaltrials_json",
  "records": [
    {
      "title": "...",
      "content_blocks": ["abstract", "methods", ...],
      "metadata": {
        "authors": ["..."],
        "journal": "...",
        "year": "...",
        "doi": "...",
        "pmid": "...",
        "url": "..."
      }
    }
  ]
}
"""
import sys
import json
import os
from pathlib import Path

def die(msg):
    sys.stderr.write(f"ERROR: {msg}\n")
    sys.exit(1)

def extract_excel(path):
    """Extract data from Excel files (.xlsx, .xls, .csv)."""
    try:
        import pandas as pd
    except ImportError:
        die("pandas not installed. Run: pip3 install pandas openpyxl")

    ext = Path(path).suffix.lower()

    if ext == '.csv':
        df = pd.read_csv(path, encoding='utf-8-sig')
    elif ext in ['.xlsx', '.xls']:
        df = pd.read_excel(path, engine='openpyxl' if ext == '.xlsx' else None)
    else:
        die(f"Unsupported Excel format: {ext}")

    records = []
    for idx, row in df.iterrows():
        # Try to map common column names to our normalized structure
        record = {
            "title": str(row.get("标题", row.get("Title", row.get("title", f"Row {idx+1}")))),
            "content_blocks": [],
            "metadata": {}
        }

        # Map common fields
        field_mapping = {
            "作者": "authors",
            "Author": "authors",
            "authors": "authors",
            "期刊": "journal",
            "Journal": "journal",
            "journal": "journal",
            "年份": "year",
            "Year": "year",
            "year": "year",
            "DOI": "doi",
            "doi": "doi",
            "PMID": "pmid",
            "pmid": "pmid",
            "URL": "url",
            "url": "url",
            "链接": "url"
        }

        for excel_col, meta_key in field_mapping.items():
            if excel_col in row and pd.notna(row[excel_col]):
                value = str(row[excel_col]).strip()
                if value:
                    if meta_key == "authors" and ";" in value:
                        record["metadata"][meta_key] = [a.strip() for a in value.split(";")]
                    elif meta_key == "authors" and "," in value:
                        record["metadata"][meta_key] = [a.strip() for a in value.split(",")]
                    else:
                        record["metadata"][meta_key] = value

        # Content blocks: look for abstract, methods, results, etc.
        content_fields = ["摘要", "Abstract", "abstract", "内容", "Content", "说明", "Description"]
        for field in content_fields:
            if field in row and pd.notna(row[field]):
                content = str(row[field]).strip()
                if content and content not in record["content_blocks"]:
                    record["content_blocks"].append(content)

        # Add any remaining fields as metadata
        for col in df.columns:
            if col not in field_mapping and col not in content_fields and pd.notna(row[col]):
                value = str(row[col]).strip()
                if value and col not in record["metadata"]:
                    record["metadata"][col] = value

        records.append(record)

    return {
        "source_type": "excel",
        "file": str(path),
        "records": records
    }

def extract_cnki_refworks(path):
    """Extract 知网 Refworks export format."""
    records = []
    current_record = None

    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                if current_record:
                    records.append(current_record)
                    current_record = None
                continue

            if line.startswith("TY  - "):  # New record
                if current_record:
                    records.append(current_record)
                current_record = {
                    "title": "",
                    "content_blocks": [],
                    "metadata": {"type": line[6:].strip()}
                }
            elif current_record:
                if line.startswith("TI  - ") or line.startswith("T1  - "):
                    current_record["title"] = line[6:].strip()
                elif line.startswith("AU  - ") or line.startswith("A1  - "):
                    if "authors" not in current_record["metadata"]:
                        current_record["metadata"]["authors"] = []
                    current_record["metadata"]["authors"].append(line[6:].strip())
                elif line.startswith("JO  - ") or line.startswith("T2  - "):
                    current_record["metadata"]["journal"] = line[6:].strip()
                elif line.startswith("PY  - ") or line.startswith("Y1  - "):
                    current_record["metadata"]["year"] = line[6:].strip()
                elif line.startswith("DO  - "):
                    current_record["metadata"]["doi"] = line[6:].strip()
                elif line.startswith("UR  - "):
                    current_record["metadata"]["url"] = line[6:].strip()
                elif line.startswith("AB  - ") or line.startswith("N2  - "):
                    current_record["content_blocks"].append(line[6:].strip())
                elif line.startswith("KW  - "):
                    if "keywords" not in current_record["metadata"]:
                        current_record["metadata"]["keywords"] = []
                    current_record["metadata"]["keywords"].append(line[6:].strip())

    if current_record:
        records.append(current_record)

    return {
        "source_type": "cnki_refworks",
        "file": str(path),
        "records": records
    }

def extract_clinicaltrials_json(path):
    """Extract ClinicalTrials.gov API JSON response."""
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    records = []

    # Handle different API response formats
    studies = []
    if isinstance(data, dict):
        if "studies" in data:
            studies = data["studies"]
        elif "Study" in data:
            studies = [data]
        elif "FullStudy" in data:
            studies = data.get("FullStudy", [])
    elif isinstance(data, list):
        studies = data

    for study in studies:
        # Navigate nested structure
        protocol_section = study.get("protocolSection", study.get("ProtocolSection", {}))
        identification = protocol_section.get("identificationModule", protocol_section.get("IdentificationModule", {}))
        description = protocol_section.get("descriptionModule", protocol_section.get("DescriptionModule", {}))

        nct_id = identification.get("nctId", identification.get("NCTId", ""))
        title = identification.get("officialTitle", identification.get("briefTitle", ""))

        record = {
            "title": title,
            "content_blocks": [],
            "metadata": {
                "nct_id": nct_id,
                "study_type": identification.get("studyType", ""),
                "phase": protocol_section.get("designModule", {}).get("phases", [""])[0] if protocol_section.get("designModule") else "",
                "url": f"https://clinicaltrials.gov/study/{nct_id}" if nct_id else ""
            }
        }

        # Extract content
        if description.get("briefSummary"):
            record["content_blocks"].append(description["briefSummary"])
        if description.get("detailedDescription"):
            record["content_blocks"].append(description["detailedDescription"])

        records.append(record)

    return {
        "source_type": "clinicaltrials_json",
        "file": str(path),
        "records": records
    }

def auto_detect_type(path):
    """Auto-detect file type based on extension and content."""
    ext = Path(path).suffix.lower()

    if ext in ['.xlsx', '.xls', '.csv']:
        return 'excel'
    elif ext == '.json':
        # Peek at content
        with open(path, 'r', encoding='utf-8') as f:
            try:
                data = json.load(f)
                if isinstance(data, dict) and ("studies" in data or "Study" in data or "FullStudy" in data):
                    return 'clinicaltrials'
            except:
                pass
        return 'json'
    elif ext in ['.txt', '.ris', '.nbib']:
        # Check if it's Refworks/RIS format
        with open(path, 'r', encoding='utf-8') as f:
            first_line = f.readline().strip()
            if first_line.startswith("TY  - "):
                return 'cnki'

    return None

def main():
    if len(sys.argv) < 2:
        die("usage: python3 extract_structured.py input_file [--type excel|cnki|clinicaltrials]")

    input_path = sys.argv[1]
    if not os.path.exists(input_path):
        die(f"input not found: {input_path}")

    # Parse --type argument
    source_type = None
    if len(sys.argv) > 2 and sys.argv[2] == '--type' and len(sys.argv) > 3:
        source_type = sys.argv[3]
    else:
        source_type = auto_detect_type(input_path)

    if not source_type:
        die(f"Could not auto-detect type. Please specify with --type")

    # Extract based on type
    if source_type == 'excel':
        result = extract_excel(input_path)
    elif source_type in ['cnki', 'cnki_refworks']:
        result = extract_cnki_refworks(input_path)
    elif source_type in ['clinicaltrials', 'clinicaltrials_json']:
        result = extract_clinicaltrials_json(input_path)
    else:
        die(f"Unsupported type: {source_type}")

    # Output JSON
    print(json.dumps(result, ensure_ascii=False, indent=2))

    # Summary to stderr
    sys.stderr.write(f"\nExtracted {len(result['records'])} records from {source_type} source\n")
    sys.stderr.write(f"Source file: {input_path}\n")

if __name__ == "__main__":
    main()
