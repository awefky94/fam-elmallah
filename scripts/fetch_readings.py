"""يقرا إيميلات «Telemetry Mail» من ياهو (IMAP) ويطلّع صف «فم الملاح» من الملف المرفق،
ويحدّث data.json (آخر 7 أيام، من الأحدث للأقدم).

المتغيرات المطلوبة (GitHub Actions secrets):
  YAHOO_USER          إيميل ياهو (افتراضي gharbiadir@yahoo.com)
  YAHOO_APP_PASSWORD  App password من إعدادات أمان ياهو (مش الباسورد العادي)
"""
import email, imaplib, json, os, sys
from datetime import datetime, timedelta, timezone
from email.header import decode_header
from html.parser import HTMLParser

SITE = 'فم الملاح'
DATA = os.path.join(os.path.dirname(__file__), '..', 'data.json')
CAIRO = timezone(timedelta(hours=2))


class Rows(HTMLParser):
    def __init__(self):
        super().__init__(); self.rows = []; self.row = None; self.cell = None
    def handle_starttag(self, tag, a):
        if tag == 'tr': self.row = []
        elif tag in ('td', 'th') and self.row is not None: self.cell = ''
    def handle_endtag(self, tag):
        if tag in ('td', 'th') and self.cell is not None:
            self.row.append(self.cell.strip()); self.cell = None
        elif tag == 'tr' and self.row is not None:
            self.rows.append(self.row); self.row = None
    def handle_data(self, d):
        if self.cell is not None: self.cell += d


def num(t):
    t = (t or '').strip().translate(str.maketrans('٠١٢٣٤٥٦٧٨٩', '0123456789'))
    try: v = float(t)
    except ValueError: return None
    return None if v >= 100 else v


def parse_html(text):
    p = Rows(); p.feed(text)
    for c in p.rows:
        if len(c) >= 6 and c[1] == SITE:
            try: h = int(c[2])
            except ValueError: return None
            d = c[3]
            if len(d) != 10 or not 0 <= h <= 23: return None
            return {'k': f'{d}_{h:02d}', 'd': d, 'h': h, 'f': num(c[4]), 'b': num(c[5])}
    return None


def html_parts(msg):
    for part in msg.walk():
        name = part.get_filename() or ''
        if name:
            dh = decode_header(name)[0]
            name = dh[0].decode(dh[1] or 'utf-8', 'ignore') if isinstance(dh[0], bytes) else dh[0]
        ctype = part.get_content_type()
        if name.lower().endswith(('.html', '.htm')) or (ctype == 'text/html' and part.get('Content-Disposition', '').startswith('attachment')):
            payload = part.get_payload(decode=True)
            if payload:
                yield payload.decode(part.get_content_charset() or 'utf-8', 'ignore')


def fetch(user, pw, days=2, limit=40):
    m = imaplib.IMAP4_SSL('imap.mail.yahoo.com', 993)
    m.login(user, pw)
    m.select('INBOX', readonly=True)
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime('%d-%b-%Y')
    typ, ids = m.search(None, 'SINCE', since, 'SUBJECT', '"Telemetry Mail"')
    ids = ids[0].split()[-limit:]
    out = []
    for i in ids:
        typ, data = m.fetch(i, '(BODY.PEEK[])')
        if typ != 'OK' or not data or not data[0]: continue
        msg = email.message_from_bytes(data[0][1])
        for html in html_parts(msg):
            r = parse_html(html)
            if r: out.append(r); break
    m.logout()
    return out


def merge(new):
    try: cur = json.load(open(DATA, encoding='utf-8'))
    except FileNotFoundError: cur = {'site': SITE, 'readings': []}
    by = {r['k']: r for r in cur.get('readings', [])}
    changed = False
    for r in new:
        if by.get(r['k']) != r: by[r['k']] = r; changed = True
    cutoff = (datetime.now(CAIRO) - timedelta(days=7)).strftime('%Y-%m-%d_%H')
    keep = {k: v for k, v in by.items() if k >= cutoff}
    if len(keep) != len(by): changed = True
    if not changed: return False
    cur['site'] = SITE
    cur['updated'] = datetime.now(CAIRO).isoformat(timespec='seconds')
    cur['readings'] = sorted(keep.values(), key=lambda r: r['k'], reverse=True)
    with open(DATA, 'w', encoding='utf-8') as f:
        json.dump(cur, f, ensure_ascii=False, indent=1); f.write('\n')
    return True


if __name__ == '__main__':
    if len(sys.argv) > 1:  # اختبار: python fetch_readings.py file.html
        print(parse_html(open(sys.argv[1], encoding='utf-8').read())); sys.exit()
    pw = os.environ.get('YAHOO_APP_PASSWORD')
    if not pw: sys.exit('YAHOO_APP_PASSWORD secret is missing')
    rows = fetch(os.environ.get('YAHOO_USER') or 'gharbiadir@yahoo.com', pw)
    print(f'found {len(rows)} readings; latest:', max(rows, key=lambda r: r['k']) if rows else None)
    print('updated' if merge(rows) else 'no change')
