"""يقرا إيميلات «Telemetry Mail» من ياهو (IMAP) ويطلّع صفوف المواقع من الملف المرفق:
  «فم الملاح» -> data.json · «حجز صرد» -> sard.json · «حجز بلتاج» -> beltag.json
  + qotour.json (إشعار مشترك لصرد وبلتاج لتطبيق «مناسيب قطور»)
(آخر 7 أيام، من الأحدث للأقدم).

المتغيرات المطلوبة (GitHub Actions secrets):
  YAHOO_USER          إيميل ياهو (افتراضي gharbiadir@yahoo.com)
  YAHOO_APP_PASSWORD  App password من إعدادات أمان ياهو (مش الباسورد العادي)
"""
import email, imaplib, json, os, sys
from datetime import datetime, timedelta, timezone
from email.header import decode_header
from html.parser import HTMLParser

ROOT = os.path.join(os.path.dirname(__file__), '..')
# الاسم المعروض، الملف، ودالة بتعرف الصف (الاسم في الإيميل ممكن يختلف في المسافات/الهمزات)
def norm(t):
    t = (t or '').replace('\u0640', '')
    for a, b in (('أ', 'ا'), ('إ', 'ا'), ('آ', 'ا'), ('ة', 'ه'), ('ى', 'ي')): t = t.replace(a, b)
    return ''.join(t.split())
SITES = [
    {'name': 'فم الملاح', 'file': 'data.json', 'match': lambda n: n == norm('فم الملاح')},
    {'name': 'حجز صرد', 'file': 'sard.json', 'match': lambda n: 'صرد' in n},
    {'name': 'حجز بلتاج', 'file': 'beltag.json', 'match': lambda n: 'بلتاج' in n},
]
# ملف إشعار تطبيق «مناسيب قطور» (حجز صرد + حجز بلتاج في إشعار واحد)
GROUP = {'file': 'qotour.json', 'title': 'مناسيب قطور', 'sites': ['حجز صرد', 'حجز بلتاج']}
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


def row_reading(c):
    try: h = int(c[2])
    except ValueError: return None
    d = c[3]
    if len(d) != 10 or not 0 <= h <= 23: return None
    return {'k': f'{d}_{h:02d}', 'd': d, 'h': h, 'f': num(c[4]), 'b': num(c[5])}


def parse_html(text):
    """بيرجع {اسم الموقع: قراءة} لكل المواقع اللي في SITES."""
    p = Rows(); p.feed(text); out = {}
    for c in p.rows:
        if len(c) < 6: continue
        n = norm(c[1])
        for st in SITES:
            if st['name'] in out or not st['match'](n): continue
            r = row_reading(c)
            if r: out[st['name']] = r
    return out


def parse_all(text):
    """كل صفوف الإيميل بالترتيب: [(الاسم المعروض, قراءة)] — لصفحة «كل القناطر» (all.json)."""
    p = Rows(); p.feed(text); out = []; seen = set()
    for c in p.rows:
        if len(c) < 6: continue
        name = ' '.join(c[1].split()); n = norm(name)
        if not n or n in seen: continue
        r = row_reading(c)
        if r: seen.add(n); out.append((name, r))
    return out


def all_names(text):
    p = Rows(); p.feed(text)
    return [c[1] for c in p.rows if len(c) >= 6]


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
    out = {st['name']: [] for st in SITES}; shown = False
    allr = {}  # norm(name) -> {'name', 'order', 'readings'}
    for i in ids:
        typ, data = m.fetch(i, '(BODY.PEEK[])')
        if typ != 'OK' or not data or not data[0]: continue
        msg = email.message_from_bytes(data[0][1])
        for html in html_parts(msg):
            got = parse_html(html)
            if not shown and len(got) < len(SITES):
                print('names in file:', ' | '.join(all_names(html))); shown = True
            for k, r in got.items(): out[k].append(r)
            try:  # جزء «كل القناطر» — أي خطأ فيه ميأثرش على الملفات القديمة
                for i2, (name, r) in enumerate(parse_all(html)):
                    e = allr.setdefault(norm(name), {'name': name, 'order': i2, 'readings': []})
                    e['readings'].append(r)
            except Exception as ex: print('all.json parse error:', ex)
            if got: break
    m.logout()
    return out, allr


def hour_label(h):
    return f"{h % 12 or 12} {'ص' if h < 12 else 'م'}"


def fmt(v):
    return '—' if v is None else f'{v:.2f}'


def delta(cur, prev):
    if cur is None or prev is None: return ''
    d = round((cur - prev) * 100)
    if d == 0: return ' (ثابت)'
    return f' (▲{d} سم)' if d > 0 else f' (▼{-d} سم)'


def notify_block(readings, site):
    """نص الإشعار اللي التطبيق بيعرضه لما قراءة جديدة توصل."""
    if not readings: return None
    L = readings[0]; P = readings[1] if len(readings) > 1 else None
    dd = L['d'][8:10] + '/' + L['d'][5:7]
    return {
        'k': L['k'],
        'title': f"{site} — الخلف {fmt(L['b'])}{delta(L['b'], P and P['b'])}",
        'text': f"قراءة الساعة {hour_label(L['h'])} ({dd})\nالخلف {fmt(L['b'])}{delta(L['b'], P and P['b'])} · الأمام {fmt(L['f'])}",
    }


def merge(new, site, fname):
    DATA = os.path.join(ROOT, fname)
    try: cur = json.load(open(DATA, encoding='utf-8'))
    except FileNotFoundError: cur = {'site': site, 'readings': []}
    by = {r['k']: r for r in cur.get('readings', [])}
    changed = False
    for r in new:
        if by.get(r['k']) != r: by[r['k']] = r; changed = True
    cutoff = (datetime.now(CAIRO) - timedelta(days=7)).strftime('%Y-%m-%d_%H')
    keep = {k: v for k, v in by.items() if k >= cutoff}
    if len(keep) != len(by): changed = True
    readings = sorted(keep.values(), key=lambda r: r['k'], reverse=True)
    nb = notify_block(readings, site)
    if cur.get('notify') != nb: changed = True
    if not changed: return False
    cur['site'] = site
    cur['updated'] = datetime.now(CAIRO).isoformat(timespec='seconds')
    cur['readings'] = readings
    cur['notify'] = nb
    with open(DATA, 'w', encoding='utf-8') as f:
        json.dump(cur, f, ensure_ascii=False, indent=1); f.write('\n')
    return True


def write_group():
    """qotour.json: آخر قراءة لكل حجز في إشعار واحد؛ المفتاح بيتغير لما أي حجز ييجي له قراءة جديدة."""
    keys, titles, lines = [], [], []
    for name in GROUP['sites']:
        st = next(x for x in SITES if x['name'] == name)
        try: d = json.load(open(os.path.join(ROOT, st['file']), encoding='utf-8'))
        except FileNotFoundError: continue
        nb = d.get('notify')
        if not nb: continue
        keys.append(nb['k'])
        R = d['readings']; L = R[0]; P = R[1] if len(R) > 1 else None
        short = name.replace('حجز ', '')
        titles.append(f"{short} {fmt(L['b'])}")
        dd = L['d'][8:10] + '/' + L['d'][5:7]
        lines.append(f"{name}: الخلف {fmt(L['b'])}{delta(L['b'], P and P['b'])} · الساعة {hour_label(L['h'])} ({dd})")
    if not keys: return False
    nb = {'k': '|'.join(keys), 'title': f"{GROUP['title']} — " + ' · '.join(titles), 'text': '\n'.join(lines)}
    path = os.path.join(ROOT, GROUP['file'])
    try: cur = json.load(open(path, encoding='utf-8'))
    except FileNotFoundError: cur = {}
    if cur.get('notify') == nb: return False
    with open(path, 'w', encoding='utf-8') as f:
        json.dump({'updated': datetime.now(CAIRO).isoformat(timespec='seconds'), 'notify': nb}, f, ensure_ascii=False, indent=1); f.write('\n')
    return True


def merge_all(allr, hours=48):
    """all.json: كل القناطر اللي في الإيميل، آخر 48 ساعة لكل واحدة."""
    path = os.path.join(ROOT, 'all.json')
    try: cur = json.load(open(path, encoding='utf-8'))
    except FileNotFoundError: cur = {'sites': []}
    sites = {norm(x['name']): x for x in cur.get('sites', [])}
    for n, e in allr.items():
        x = sites.setdefault(n, {'name': e['name'], 'order': e['order'], 'readings': []})
        x['order'] = e['order']
        by = {r['k']: r for r in x['readings']}
        for r in e['readings']: by[r['k']] = r
        x['readings'] = list(by.values())
    cutoff = (datetime.now(CAIRO) - timedelta(hours=hours)).strftime('%Y-%m-%d_%H')
    out = []
    for x in sites.values():
        x['readings'] = sorted((r for r in x['readings'] if r['k'] >= cutoff), key=lambda r: r['k'], reverse=True)
        if x['readings']: out.append(x)
    out.sort(key=lambda x: x['order'])
    if out == cur.get('sites'): return False
    with open(path, 'w', encoding='utf-8') as f:
        json.dump({'updated': datetime.now(CAIRO).isoformat(timespec='seconds'), 'sites': out}, f, ensure_ascii=False, separators=(',', ':')); f.write('\n')
    return True


if __name__ == '__main__':
    if len(sys.argv) > 1:  # اختبار: python fetch_readings.py file.html
        print(parse_html(open(sys.argv[1], encoding='utf-8').read())); sys.exit()
    pw = os.environ.get('YAHOO_APP_PASSWORD')
    if not pw: sys.exit('YAHOO_APP_PASSWORD secret is missing')
    lim = int(os.environ.get('FETCH_LIMIT') or 40)
    rows, allr = fetch(os.environ.get('YAHOO_USER') or 'gharbiadir@yahoo.com', pw, days=2 if lim > 10 else 1, limit=lim)
    for st in SITES:
        rs = rows[st['name']]
        print(f"{st['name']}: found {len(rs)} readings; latest:", max(rs, key=lambda r: r['k']) if rs else None)
        print('  ', 'updated' if merge(rs, st['name'], st['file']) else 'no change')
    print('qotour:', 'updated' if write_group() else 'no change')
    try: print(f'all: {len(allr)} sites,', 'updated' if merge_all(allr) else 'no change')
    except Exception as ex: print('all.json error:', ex)
