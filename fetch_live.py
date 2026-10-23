#!/usr/bin/env python3
"""Builds live.json for Landslide: the party holding each House of Commons seat today, and an
average of the latest Great Britain voting-intention polls.

Standard library only, so it runs on a GitHub Actions runner as it is.

    python3 fetch_live.py                      # fetch everything and write live.json
    python3 fetch_live.py --members DIR --wiki FILE   # build from files already downloaded

It writes nothing if either source looks wrong (too few MPs, too few polls), so a bad day at the
source never replaces a good file. It also leaves the file alone when only the timestamp would change.
"""

import argparse
import datetime as dt
import glob
import html.parser
import json
import os
import re
import sys
import urllib.request

USER_AGENT = 'LandslideGame/1.0 (+https://github.com/MacTR11/landslide-data)'
MEMBERS_URL = ('https://members-api.parliament.uk/api/Members/Search'
               '?House=Commons&IsCurrentMember=true&skip={skip}&take=20')
POLLING_PAGE = 'Opinion_polling_for_the_next_United_Kingdom_general_election'
POLLING_URL = 'https://en.wikipedia.org/api/rest_v1/page/html/' + POLLING_PAGE
LAST_ELECTION = dt.date(2024, 7, 4)
WINDOW_DAYS = 14

# Party names as the Members API gives them, and the game's id for each.
PARTIES = {
    'Labour': 'labour', 'Labour (Co-op)': 'labour',
    'Conservative': 'conservative',
    'Liberal Democrat': 'libDem',
    'Reform UK': 'reform',
    'Green Party': 'green',
    'Scottish National Party': 'snp',
    'Plaid Cymru': 'plaid',
    'Your Party': 'yourParty',
    'Restore Britain': 'restoreBritain',
    'Sinn Féin': 'sinnFein',
    'Democratic Unionist Party': 'dup',
    'Alliance': 'alliance',
    'Social Democratic & Labour Party': 'sdlp',
    'Ulster Unionist Party': 'uup',
    'Traditional Unionist Voice': 'tuv',
    'Speaker': 'speaker',
    'Independent': 'independent',
}

# Column headings in the polling tables, and the game's id for each.
POLL_COLUMNS = {
    'Lab': 'labour', 'Con': 'conservative', 'Ref': 'reform', 'LD': 'libDem', 'Grn': 'green',
    'SNP': 'snp', 'PC': 'plaid', 'RB': 'restoreBritain', 'YP': 'yourParty', 'Others': 'others',
}

MONTHS = {m: i + 1 for i, m in enumerate(
    ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'])}


def fetch(url):
    request = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read().decode('utf-8')


# Seats

def member_pages(directory):
    if directory:
        return [json.load(open(path, encoding='utf-8')) for path in sorted(glob.glob(os.path.join(directory, '*.json')))]
    pages, skip = [], 0
    while True:
        page = json.loads(fetch(MEMBERS_URL.format(skip=skip)))
        pages.append(page)
        skip += 20
        if skip >= page['totalResults']:
            return pages


def seat_holders(pages):
    holders = {}
    for page in pages:
        for item in page['items']:
            member = item['value']
            membership = member['latestHouseMembership']
            party_name = member['latestParty']['name']
            start = dt.date.fromisoformat(membership['membershipStartDate'][:10])
            holder = {'party': PARTIES.get(party_name, 'independent')}
            if party_name not in PARTIES:
                holder['partyName'] = party_name
            if start > LAST_ELECTION:
                holder['byElection'] = start.isoformat()
            holders[membership['membershipFrom']] = holder
    if len(holders) < 600:
        sys.exit(f'Only {len(holders)} seats came back from the Members API; keeping the old file.')
    return dict(sorted(holders.items()))


# Polls

class Tables(html.parser.HTMLParser):
    """Collects every table on the page as rows of cell text, noting the section each one is in."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables, self.section = [], None
        self._heading = None
        self._row = self._cell = None
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in ('style', 'script', 'sup'):
            self._skip += 1
        elif tag in ('h2', 'h3'):
            self._heading = attrs.get('id')
        elif tag == 'table':
            self.tables.append({'section': self.section, 'rows': []})
        elif tag == 'tr' and self.tables:
            self._row = []
        elif tag in ('td', 'th') and self._row is not None:
            self._cell = []
        elif tag == 'br' and self._cell is not None:
            self._cell.append(' ')

    def handle_endtag(self, tag):
        if tag in ('style', 'script', 'sup'):
            self._skip = max(0, self._skip - 1)
        elif tag in ('h2', 'h3') and self._heading:
            self.section, self._heading = self._heading, None
        elif tag in ('td', 'th') and self._cell is not None:
            self._row.append(re.sub(r'\s+', ' ', ''.join(self._cell)).strip())
            self._cell = None
        elif tag == 'tr' and self._row is not None:
            if self._row:
                self.tables[-1]['rows'].append(self._row)
            self._row = None

    def handle_data(self, data):
        if self._cell is not None and not self._skip:
            self._cell.append(data)


def parse_dates(text, year):
    """'21–22 Oct' or '30 Sep – 2 Oct' or '28 Oct', in a table for `year`: (start, end)."""
    parts = [p.strip() for p in re.split(r'[–-]', text) if p.strip()]
    found = []
    for part in parts:
        m = re.fullmatch(r'(\d{1,2})(?: ([A-Z][a-z]{2}))?(?: (\d{4}))?', part)
        if not m:
            return None
        found.append([int(m.group(1)), m.group(2), int(m.group(3)) if m.group(3) else None])
    if not found or found[-1][1] is None or found[-1][1] not in MONTHS:
        return None
    for i in range(len(found) - 2, -1, -1):
        found[i][1] = found[i][1] or found[i + 1][1]
    end_day, end_month, end_year = found[-1]
    end = dt.date(end_year or year, MONTHS[end_month], end_day)
    start_day, start_month, start_year = found[0]
    start = dt.date(start_year or end.year, MONTHS[start_month], start_day)
    if start > end:
        start = start.replace(year=start.year - 1)
    return start, end


def percent(text):
    m = re.match(r'\s*(\d+(?:\.\d+)?)\s*%', text)
    return float(m.group(1)) if m else None


def national_polls(page_html):
    parser = Tables()
    parser.feed(page_html)
    polls = []
    for table in parser.tables:
        if not (table['section'] or '').isdigit():
            continue
        year = int(table['section'])
        header = table['rows'][0] if table['rows'] else []
        if 'Pollster' not in header:
            continue
        columns = {name.replace(' ', ''): i for i, name in enumerate(header)}
        for row in table['rows'][1:]:
            if len(row) != len(header):
                continue
            dates = parse_dates(row[columns['Date(s)conducted']] if 'Date(s)conducted' in columns else row[0], year)
            area = row[columns['Area']] if 'Area' in columns else 'GB'
            if not dates or area not in ('GB', 'UK'):
                continue
            shares = {}
            for heading, party in POLL_COLUMNS.items():
                if heading in columns:
                    value = percent(row[columns[heading]])
                    if value is not None:
                        shares[party] = value
            # Your Party is usually inside Others, with its own figure in the cell's note.
            if 'yourParty' not in shares and 'Others' in columns:
                m = re.search(r'\bYP (\d+(?:\.\d+)?)%', row[columns['Others']])
                if m:
                    shares['yourParty'] = float(m.group(1))
                    if 'others' in shares:
                        shares['others'] = max(0.0, shares['others'] - shares['yourParty'])
            if not all(p in shares for p in ('labour', 'conservative', 'reform')):
                continue
            pollster = re.sub(r'\[\d+\]', '', row[columns['Pollster']]).strip()
            sample = re.sub(r'[^\d]', '', row[columns['Samplesize']]) if 'Samplesize' in columns else ''
            polls.append({
                'pollster': pollster,
                'client': row[columns['Client']] if 'Client' in columns else '',
                'start': dates[0].isoformat(), 'end': dates[1].isoformat(),
                'area': area,
                'sample': int(sample) if sample else None,
                'shares': shares,
            })
    return polls


def polling_average(polls):
    if not polls:
        sys.exit('No polls found on the polling page; keeping the old file.')
    latest = max(dt.date.fromisoformat(p['end']) for p in polls)
    since = latest - dt.timedelta(days=WINDOW_DAYS)
    newest = {}
    for poll in sorted(polls, key=lambda p: p['end'], reverse=True):
        if dt.date.fromisoformat(poll['end']) > since and poll['pollster'] not in newest:
            newest[poll['pollster']] = poll
    used = sorted(newest.values(), key=lambda p: p['end'], reverse=True)
    if len(used) < 3:
        sys.exit(f'Only {len(used)} pollsters in the last {WINDOW_DAYS} days; keeping the old file.')
    # Pollsters that don't prompt for a party count it in Others, so each party's average is taken
    # over the polls that report it and the named parties are then scaled to fit around Others.
    average = {}
    for party in POLL_COLUMNS.values():
        values = [p['shares'][party] for p in used if party in p['shares']]
        if values:
            average[party] = sum(values) / len(values)
    others = average.pop('others', 0.0)
    scale = (100 - others) / sum(average.values())
    average = {p: round(v * scale, 1) for p, v in average.items()}
    average['others'] = round(100 - sum(average.values()), 1)
    return {
        'source': ('Opinion polling for the next United Kingdom general election, Wikipedia '
                   '(CC BY-SA 4.0), from the pollsters\' published figures'),
        'sourceURL': 'https://en.wikipedia.org/wiki/' + POLLING_PAGE,
        'method': (f'The latest Great Britain poll from each pollster with fieldwork ending in the '
                   f'{WINDOW_DAYS} days to {latest.strftime("%-d %B %Y")}, averaged with equal weight.'),
        'from': (since + dt.timedelta(days=1)).isoformat(),
        'to': latest.isoformat(),
        'average': average,
        'polls': used,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--members', help='directory of Members API pages already downloaded')
    parser.add_argument('--wiki', help='polling page HTML already downloaded')
    parser.add_argument('--out', default=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'live.json'))
    args = parser.parse_args()

    holders = seat_holders(member_pages(args.members))
    page_html = open(args.wiki, encoding='utf-8').read() if args.wiki else fetch(POLLING_URL)
    polls = polling_average(national_polls(page_html))
    data = {
        'format': 1,
        'generated': dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'seats': {
            'source': 'UK Parliament Members API',
            'licence': 'Contains Parliamentary information licensed under the Open Parliament Licence v3.0',
            'holders': holders,
        },
        'polls': polls,
    }
    if os.path.exists(args.out):
        old = json.load(open(args.out, encoding='utf-8'))
        if {k: v for k, v in old.items() if k != 'generated'} == {k: v for k, v in data.items() if k != 'generated'}:
            print('No change.')
            return
    with open(args.out, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
        f.write('\n')
    print(f'{len(holders)} seats; {len(polls["polls"])} polls from {polls["from"]} to {polls["to"]}: {polls["average"]}')


if __name__ == '__main__':
    main()
