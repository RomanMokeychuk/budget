"""Збирає курс долара в обмінниках Одеси з kurs.com.ua і зберігає в JSON.

Запускається GitHub Actions щогодини (див. .github/workflows/odesa-rates.yml).
Використання: python3 scripts/odesa_rates.py rates.json
"""
import json
import re
import statistics
import sys
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

CITY = 1551  # Одеса на kurs.com.ua
UA = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/130.0 Safari/537.36')
KYIV = ZoneInfo('Europe/Kyiv')


def get_json(url):
    req = urllib.request.Request(url, headers={
        'User-Agent': UA,
        'X-Requested-With': 'XMLHttpRequest',
        'Accept': 'application/json, text/javascript, */*; q=0.01',
        'Referer': f'https://kurs.com.ua/gorod/{CITY}-odessa/usd',
    })
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode('utf-8'))


def values(html):
    return [float(v.replace(',', '.')) for v in re.findall(r'class="value"[^>]*>\s*(\d+[,.]\d+)', html)]


def sane(buy, sell):
    return 10 < buy < 200 and 10 < sell < 200 and sell >= buy and (sell - buy) / buy < 0.1


def parse_exchangers(html):
    optimal, rows = None, []
    for seg in re.split(r'<tbody', html)[1:]:
        head = seg[:seg.find('>')]
        if 'data-impossible' in head:  # пункти, де обмін неможливий
            continue
        for tr in re.split(r'<tr', seg)[1:]:
            attrs = tr[:tr.find('>')]
            v = values(tr)
            if len(v) < 2 or not sane(v[0], v[1]):
                continue
            if 'tr-green' in attrs:
                optimal = {'buy': v[0], 'sell': v[1]}
                continue
            m = re.search(r'data-amount="(\d+)"', attrs)
            ts = re.search(r'data-sort[Vv]alue="(\d{4}-\d\d-\d\d \d\d:\d\d)"', tr)
            rows.append({'amount': int(m.group(1)) if m else 1, 'buy': v[0], 'sell': v[1],
                         'ts': ts.group(1) if ts else None})
    return optimal, rows


def main(out):
    now = datetime.now(KYIV)
    data = get_json(f'https://kurs.com.ua/ajax/organizations/cash/povs/in-city?city={CITY}&currency=1')
    optimal, rows = parse_exchangers(data['view']['all'])
    retail = [r for r in rows if r['amount'] == 1]
    today = now.strftime('%Y-%m-%d')
    fresh = [r for r in retail if r['ts'] and r['ts'].startswith(today)] or retail
    if not fresh:
        raise SystemExit('Не знайдено курсів обмінників — можливо, змінилась структура сайту')

    result = {
        'city': 'Одеса',
        'source': 'kurs.com.ua',
        'updated': now.isoformat(timespec='minutes'),
        'usd': {
            'buy': round(statistics.median(r['buy'] for r in fresh), 3),
            'sell': round(statistics.median(r['sell'] for r in fresh), 3),
            'best_buy': max(r['buy'] for r in fresh),    # найдорожче продати свої $
            'best_sell': min(r['sell'] for r in fresh),  # найдешевше купити $
            'count': len(fresh),
            'optimal': optimal,
        },
    }
    try:  # середній курс банків Одеси (необовʼязково)
        b = values(get_json(f'https://kurs.com.ua/ajax/currency/rates?type=main&currency_from=1&city={CITY}')['view'])
        if len(b) >= 2 and sane(b[0], b[1]):
            result['banks'] = {'buy': b[0], 'sell': b[1]}
    except Exception as e:  # noqa: BLE001
        print('banks: skip', e, file=sys.stderr)

    with open(out, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else 'rates.json')
