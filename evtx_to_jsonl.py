#!/usr/bin/env python3
"""
evtx_to_jsonl.py
Usage:
  python3 evtx_to_jsonl.py input.evtx > events.jsonl
Produces one JSON object per line, with EventID and flattened EventData fields.

Some Important Sysmon Event ID:
    <!-- ── Core execution events ────────────────────────────────────── -->
    <ProcessCreate      onmatch="include"/>   <!-- Event ID 1 -->
    <ProcessTerminate   onmatch="include"/>   <!-- Event ID 5 -->
    <DriverLoad         onmatch="include"/>   <!-- Event ID 6 -->
    <ProcessAccess      onmatch="include"/>   <!-- Event ID 10 -->
    
    <!-- ── File system events ───────────────────────────────────────── -->
    <FileCreateTime     onmatch="include"/>   <!-- Event ID 2 -->
    <FileCreate         onmatch="include"/>   <!-- Event ID 11 -->
    <FileDelete         onmatch="include"/>   <!-- Event ID 23 -->
    
    <!-- ── Registry events ──────────────────────────────────────────── -->
    <RegistryEvent      onmatch="include"/>   <!-- Event IDs 12, 13, 14 -->
    
    <!-- ── Networking & name resolution ─────────────────────────────── -->
    <NetworkConnect     onmatch="include"/>   <!-- Event ID 3  (all ports) -->
    <DnsQuery           onmatch="include"/>   <!-- Event ID 22 -->
    
    <!-- ── Clipboard monitoring ─────────────────────────────────────── -->
    <ClipboardChange    onmatch="include"/>   <!-- Event ID 24 -->

"""
import sys
import json
from Evtx.Evtx import Evtx
import xmltodict

def get_text(node):
    """Get text value from xmltodict node which can be str or dict like {'#text':...}"""
    if node is None:
        return None
    if isinstance(node, str):
        return node
    if isinstance(node, dict):
        # common keys: '#text' or '@Value' etc
        return node.get('#text') or next(iter(node.values()), None)
    return str(node)

def extract_system_fields(system):
    out = {}
    # common System fields
    out['EventRecordID'] = get_text(system.get('EventRecordID'))
    out['EventID'] = get_text(system.get('EventID'))
    out['ProviderName'] = get_text(system.get('Provider', {}).get('@Name')) if system.get('Provider') else None
    timecreated = system.get('TimeCreated')
    if isinstance(timecreated, dict):
        out['TimeCreated'] = timecreated.get('@SystemTime') or get_text(timecreated)
    else:
        out['TimeCreated'] = get_text(timecreated)
    out['Level'] = get_text(system.get('Level'))
    out['Task'] = get_text(system.get('Task'))
    out['Opcode'] = get_text(system.get('Opcode'))
    out['Keywords'] = get_text(system.get('Keywords'))
    out['Computer'] = get_text(system.get('Computer'))
    out['Channel'] = get_text(system.get('Channel'))
    return out

def flatten_eventdata(eventdata):
    """Return dict of Name->value for EventData/Data elements."""
    out = {}
    if not eventdata:
        return out
    dat = eventdata.get('Data')
    if dat is None:
        return out
    # dat can be list or single dict/str
    if isinstance(dat, list):
        for item in dat:
            # item may be like {'@Name':'CommandLine','#text':'...'} or simple string
            if isinstance(item, dict):
                name = item.get('@Name') or item.get('Name') or None
                val = get_text(item)
                # if no name, create incremental keys
                if name:
                    out[name] = val
                else:
                    # fallback key
                    out_key = "_unnamed_data_%d" % len(out)
                    out[out_key] = val
            else:
                out_key = "_unnamed_data_%d" % len(out)
                out[out_key] = get_text(item)
    else:
        # single data element
        item = dat
        if isinstance(item, dict):
            name = item.get('@Name') or item.get('Name') or None
            val = get_text(item)
            if name:
                out[name] = val
            else:
                out["_unnamed_data_0"] = val
        else:
            out["_unnamed_data_0"] = get_text(item)
    return out

def main(fn):
    with Evtx(fn) as evtx:
        for record in evtx.records():
            try:
                xml = record.xml()
                doc = xmltodict.parse(xml)
                evt = doc.get('Event') or {}
                system = evt.get('System', {})
                eventdata = evt.get('EventData', {})
                # system fields
                base = extract_system_fields(system)
                # flatten EventData
                ed = flatten_eventdata(eventdata)
                # also try to capture top-level EventData text fields if any
                # combine
                base.update(ed)
                # include original Event XML? (optional)
                # base['_raw_xml'] = xml
                print(json.dumps(base, ensure_ascii=False))
            except Exception as e:
                # don't stop on parse error; log to stderr
                print(f"Warning: failed to parse record: {e}", file=sys.stderr)
                continue

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: evtx_to_jsonl.py input.evtx > events.jsonl", file=sys.stderr)
        sys.exit(1)
    main(sys.argv[1])
