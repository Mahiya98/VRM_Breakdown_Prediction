"""Download the 'VRM Breakdown' tab of the Google Sheet as CSV.
Preferred: a service account (secret GOOGLE_SERVICE_ACCOUNT_JSON) that the sheet is shared with as Viewer - no public link needed.
Fallback: the sheet shared as 'Anyone with the link can view' (uses the gviz CSV endpoint).
Env: SHEET_ID (required), SHEET_TAB (default 'VRM Breakdown'). Usage: python fetch_sheet.py out.csv"""
import os, sys, io, json, csv, urllib.parse, requests
sid = os.environ['SHEET_ID'].strip(); tab = os.environ.get('SHEET_TAB', 'VRM Breakdown'); out = sys.argv[1] if len(sys.argv) > 1 else 'breakdown.csv'
sa = os.environ.get('GOOGLE_SERVICE_ACCOUNT_JSON', '').strip()
if sa:
    from google.oauth2 import service_account
    from google.auth.transport.requests import Request
    creds = service_account.Credentials.from_service_account_info(json.loads(sa), scopes=['https://www.googleapis.com/auth/spreadsheets.readonly']); creds.refresh(Request())
    r = requests.get(f'https://sheets.googleapis.com/v4/spreadsheets/{sid}/values/{urllib.parse.quote(tab)}', params={'valueRenderOption': 'FORMATTED_VALUE'}, headers={'Authorization': f'Bearer {creds.token}'}, timeout=120)
    r.raise_for_status(); rows = r.json().get('values', []); w = len(rows[0]) if rows else 0
    with open(out, 'w', newline='', encoding='utf-8') as f: csv.writer(f).writerows([row + [''] * (w - len(row)) for row in rows])
else:
    r = requests.get(f'https://docs.google.com/spreadsheets/d/{sid}/gviz/tq', params={'tqx': 'out:csv', 'sheet': tab}, timeout=120); r.raise_for_status()
    open(out, 'wb').write(r.content)
head = open(out, encoding='utf-8').readline(); n = sum(1 for _ in open(out, encoding='utf-8'))
if 'Breakdwon Name' not in head or n < 1000: sys.exit(f'Unexpected sheet content (header={head[:80]!r}, lines={n}); refusing to overwrite forecasts.')
print(f'fetched {n} lines from tab {tab!r}')
