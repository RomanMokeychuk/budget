"""Щоденні нагадування про платежі в Telegram.

Читає data.json з приватного репозиторію даних і надсилає повідомлення,
якщо наближається платіж: кредитка (мінімальний платіж), регулярний платіж
або борг із датою повернення.

Змінні середовища (GitHub → Settings → Secrets and variables → Actions):
  DATA_TOKEN    — токен з доступом до репозиторію даних (той самий, що в телефоні)
  TG_BOT_TOKEN  — токен Telegram-бота від @BotFather
  TG_CHAT_IDS   — chat id через кому (якщо порожньо — скрипт покаже їх у лозі)
  DATA_REPO     — необовʼязково, за замовчуванням <власник>/family-budget-data
  FORCE         — "true", щоб надіслати повідомлення навіть без термінових платежів
"""
import json
import math
import os
import sys
import urllib.request
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

KYIV = ZoneInfo('Europe/Kyiv')
CUR = {'UAH': '₴', 'USD': '$'}
MONTHS = ['січня', 'лютого', 'березня', 'квітня', 'травня', 'червня', 'липня',
          'серпня', 'вересня', 'жовтня', 'листопада', 'грудня']


def http(url, headers=None, data=None):
    req = urllib.request.Request(url, headers=headers or {}, data=data)
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode('utf-8')


def load_data():
    owner = os.environ.get('GITHUB_REPOSITORY_OWNER', 'RomanMokeychuk')
    repo = os.environ.get('DATA_REPO') or f'{owner}/family-budget-data'
    path = os.environ.get('DATA_PATH') or 'data.json'
    txt = http(f'https://api.github.com/repos/{repo}/contents/{path}', {
        'Authorization': 'Bearer ' + os.environ['DATA_TOKEN'],
        'Accept': 'application/vnd.github.raw+json',
        'X-GitHub-Api-Version': '2022-11-28'})
    return json.loads(txt)


def live(d, col):
    return [r for r in (d.get('col', {}).get(col) or {}).values() if not r.get('del')]


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def dim(y, m):
    return ((date(y + (m == 12), m % 12 + 1, 1)) - timedelta(days=1)).day


def due_in(y, m, day):
    return date(y, m, min(int(day), dim(y, m)) if day else dim(y, m))


def next_ym(y, m):
    return (y + 1, 1) if m == 12 else (y, m + 1)


def money(v, cur='UAH'):
    s = f'{v:,.2f}'.replace(',', ' ').replace('.', ',')
    if s.endswith(',00'):
        s = s[:-3]
    return f'{s} {CUR.get(cur, "₴")}'


def fmt_date(d):
    return f'{d.day} {MONTHS[d.month - 1]}'


def balances(d):
    acc = {a['id']: num(a.get('init')) for a in live(d, 'acc')}
    for t in live(d, 'tx'):
        v = num(t.get('amount'))
        if t.get('type') == 'inc' and t.get('acc') in acc:
            acc[t['acc']] += v
        elif t.get('type') == 'exp' and t.get('acc') in acc:
            acc[t['acc']] -= v
        elif t.get('type') == 'tr':
            if t.get('acc') in acc:
                acc[t['acc']] -= v
            if t.get('acc2') in acc:
                a2 = t.get('amount2')
                acc[t['acc2']] += num(a2) if a2 not in (None, '') else v
    return acc


def min_pay(a, debt):
    if debt <= 0:
        return 0.0
    pct = 5.0 if a.get('minPct') in (None, '') else num(a.get('minPct'))
    return min(debt, max(math.ceil(debt * pct / 100), num(a.get('minFloor'))))


def upcoming(d, today):
    ym = f'{today.year}-{today.month:02d}'
    bal = balances(d)
    tx = live(d, 'tx')
    out = []
    for a in live(d, 'acc'):
        if a.get('kind') != 'credit':
            continue
        cur = a.get('cur') or 'UAH'
        debt = max(0.0, -bal.get(a['id'], 0.0))
        paid = sum((num(t.get('amount2')) if t.get('type') == 'tr' and t.get('amount2') not in (None, '') else num(t.get('amount')))
                   for t in tx if str(t.get('date', '')).startswith(ym) and
                   ((t.get('type') == 'tr' and t.get('acc2') == a['id']) or (t.get('type') == 'inc' and t.get('acc') == a['id'])))
        bank = a.get('bank') if isinstance(a.get('bank'), dict) and a['bank'].get('ym') == ym else None
        mn = num(bank['min']) if bank and bank.get('min') not in (None, '') else min_pay(a, debt + paid)
        left = 0.0 if a.get('minPaid') == ym else max(0.0, round(mn - paid, 2))
        grace = max(0.0, round(num(bank['grace']) - paid, 2)) if bank and bank.get('grace') not in (None, '') else None
        rate = num(a.get('rate'))
        if grace is not None:
            extra = f'💚 без відсотків, якщо внести {money(grace, cur)}'
        else:
            extra = f'повний борг {money(debt, cur)}' + (f', ~{money(debt * rate / 100, cur)} відсотків/міс' if rate and debt else '')
        if left > 0:
            out.append(('💳', a.get('name', 'Кредитка'), left, cur, due_in(today.year, today.month, a.get('payDay')), 'мінімальний платіж; ' + extra))
        elif debt > 0:
            y, m = next_ym(today.year, today.month)
            out.append(('💳', a.get('name', 'Кредитка'), min_pay(a, debt), cur, due_in(y, m, a.get('payDay')), 'мінімальний платіж (цього місяця вже внесено); ' + extra))
    for r in live(d, 'rec'):
        if (r.get('paid') or {}).get(ym):
            continue
        out.append(('🔁', r.get('name', 'Платіж'), num(r.get('amount')), 'UAH', due_in(today.year, today.month, r.get('day') or 1), 'регулярний платіж'))
    for x in live(d, 'debt'):
        if x.get('dir') != 'owe' or not x.get('due'):
            continue
        left = num(x.get('total')) - sum(num(p.get('amount')) for p in x.get('payments') or [])
        if left > 0:
            out.append(('🤝', x.get('person', 'Борг'), left, 'UAH', date.fromisoformat(x['due'][:10]), 'повернути борг'))
    res = [dict(icon=i, name=n, amount=v, cur=c, due=du, note=nt, days=(du - today).days) for i, n, v, c, du, nt in out]
    return sorted(res, key=lambda x: x['days'])


def when(days):
    if days < 0:
        return f'⚠️ прострочено на {-days} дн.'
    if days == 0:
        return '❗ сьогодні'
    if days == 1:
        return '❗ завтра'
    return f'через {days} дн.'


def build_message(items, remind_days, force):
    urgent = [x for x in items if x['days'] <= remind_days]
    if not urgent and not force:
        return None
    lines = ['⏰ <b>Нагадування про платежі</b>', '']
    for x in (urgent or [x for x in items if x['days'] <= 31]):
        lines.append(f"{x['icon']} <b>{x['name']}</b> — <b>{money(x['amount'], x['cur'])}</b>")
        lines.append(f"    до {fmt_date(x['due'])} ({when(x['days'])})")
        lines.append(f"    <i>{x['note']}</i>")
    if not urgent:
        lines.insert(2, '✅ Термінових платежів немає. Найближчі:\n')
    if len(lines) == 2:
        lines.append('Платежів найближчим часом немає ✅')
    return '\n'.join(lines)


def tg(method, payload=None):
    tok = os.environ['TG_BOT_TOKEN']
    data = json.dumps(payload).encode() if payload else None
    return json.loads(http(f'https://api.telegram.org/bot{tok}/{method}', {'Content-Type': 'application/json'}, data))


def main():
    today = datetime.now(KYIV).date()
    force = os.environ.get('FORCE', '').lower() in ('1', 'true', 'yes')
    chats = [c.strip() for c in os.environ.get('TG_CHAT_IDS', '').split(',') if c.strip()]
    if not chats:
        ups = tg('getUpdates').get('result', [])
        found = {}
        for u in ups:
            ch = (u.get('message') or u.get('my_chat_member') or {}).get('chat') or {}
            if ch.get('id'):
                found[ch['id']] = ch.get('first_name') or ch.get('title') or ''
        print('TG_CHAT_IDS не задано. Чати, що писали боту за останню добу:')
        for i, n in found.items():
            print(f'  {i}  ({n})')
        print('Скопіюйте потрібні id в секрет TG_CHAT_IDS через кому.')
        chats = [str(i) for i in found]
        if not chats:
            sys.exit('Напишіть боту /start у Telegram і запустіть ще раз.')

    d = load_data()
    remind_days = 3
    for s in live(d, 'setting'):
        if s.get('id') == 'remind' and isinstance(s.get('value'), dict):
            remind_days = int(s['value'].get('days', 3))
    items = upcoming(d, today)
    for x in items:
        print(x['days'], x['name'], x['amount'], x['due'])
    msg = build_message(items, remind_days, force)
    if not msg:
        print('Термінових платежів немає — нічого не надсилаю.')
        return
    for c in chats:
        tg('sendMessage', {'chat_id': c, 'text': msg, 'parse_mode': 'HTML', 'disable_web_page_preview': True})
        print('Надіслано в', c)


if __name__ == '__main__':
    main()
