#!/usr/bin/env python3
"""Builds live.json for Landslide: the party holding each House of Commons seat today, and an
average of the latest Great Britain voting-intention polls, with averages for Scotland and Wales
from their own polls.

Standard library only, so it runs on a GitHub Actions runner as it is.

    python3 fetch_live.py                      # fetch everything and write live.json
    python3 fetch_live.py --members DIR --wiki FILE --wiki-scotland FILE   # from files already downloaded

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
SCOTLAND_PAGE = 'Opinion_polling_in_Scotland_for_the_next_United_Kingdom_general_election'
WIKI_HTML = 'https://en.wikipedia.org/api/rest_v1/page/html/'
LAST_ELECTION = dt.date(2024, 7, 4)
WINDOW_DAYS = 14
# Polls of Scotland and Wales alone are rarer, so their window is longer.
NATION_WINDOW_DAYS = 60

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


def table_polls(table, year, need_area):
    """The polls in one table, as dicts. `need_area` keeps only rows whose Area is GB or UK."""
    header = table['rows'][0] if table['rows'] else []
    columns = {name.replace(' ', ''): i for i, name in enumerate(header)}
    if 'Pollster' not in columns:
        return []
    polls = []
    for row in table['rows'][1:]:
        if len(row) != len(header):
            continue
        dates = parse_dates(row[columns['Date(s)conducted']] if 'Date(s)conducted' in columns else row[0], year)
        area = row[columns['Area']] if 'Area' in columns else ''
        if not dates or (need_area and area not in ('GB', 'UK')):
            continue
        shares = {}
        for heading, party in POLL_COLUMNS.items():
            if heading in columns:
                value = percent(row[columns[heading]])
                if value is not None:
                    shares[party] = value
        # Your Party and Restore Britain are often inside Others, with their own figures in the
        # cell's note ("1% YP 0% Other 1%", "3% RB: 2% Others: 1%").
        if 'Others' in columns:
            for key, party in (('YP', 'yourParty'), ('RB', 'restoreBritain')):
                m = re.search(r'\b' + key + r':? (\d+(?:\.\d+)?)%', row[columns['Others']])
                if m and party not in shares:
                    shares[party] = float(m.group(1))
                    if 'others' in shares:
                        shares['others'] = max(0.0, shares['others'] - shares[party])
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


def page_tables(page_html):
    parser = Tables()
    parser.feed(page_html)
    return parser.tables


def national_polls(tables):
    """Great Britain polls: the tables under the year headings of the main polling page."""
    polls = []
    for table in tables:
        if (table['section'] or '').isdigit():
            polls += table_polls(table, int(table['section']), need_area=True)
    return polls


def section_polls(tables, section):
    """The polls in the first table under a heading (Wales on the main page, Polls on Scotland's).
    Scotland's page also has tables of Scottish subsamples of GB polls, a hundred or two people
    each, under year headings: those are left out."""
    for table in tables:
        if table['section'] == section:
            polls = table_polls(table, dt.date.today().year, need_area=False)
            if polls:
                return polls
    return []


def polling_average(polls, window, minimum, area, page):
    """The latest poll from each pollster with fieldwork ending in the `window` days to the newest
    one, averaged with equal weight."""
    if not polls:
        return None
    latest = max(dt.date.fromisoformat(p['end']) for p in polls)
    since = latest - dt.timedelta(days=window)
    newest = {}
    for poll in sorted(polls, key=lambda p: p['end'], reverse=True):
        if dt.date.fromisoformat(poll['end']) > since and poll['pollster'] not in newest:
            newest[poll['pollster']] = poll
    used = sorted(newest.values(), key=lambda p: p['end'], reverse=True)
    if len(used) < minimum:
        return None
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
    title = page.replace('_', ' ')
    return {
        'source': f'{title}, Wikipedia (CC BY-SA 4.0), from the pollsters\' published figures',
        'sourceURL': 'https://en.wikipedia.org/wiki/' + page,
        'method': (f'The latest {area} poll from each pollster with fieldwork ending in the '
                   f'{window} days to {latest.strftime("%-d %B %Y")}, averaged with equal weight.'),
        'from': (since + dt.timedelta(days=1)).isoformat(),
        'to': latest.isoformat(),
        'average': average,
        'polls': used,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--members', help='directory of Members API pages already downloaded')
    parser.add_argument('--wiki', help='polling page HTML already downloaded')
    parser.add_argument('--wiki-scotland', help="Scotland's polling page HTML already downloaded")
    parser.add_argument('--out', default=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'live.json'))
    args = parser.parse_args()

    holders = seat_holders(member_pages(args.members))
    main_tables = page_tables(open(args.wiki, encoding='utf-8').read() if args.wiki else fetch(WIKI_HTML + POLLING_PAGE))
    polls = polling_average(national_polls(main_tables), WINDOW_DAYS, 3, 'Great Britain', POLLING_PAGE)
    if not polls:
        sys.exit(f'Fewer than three pollsters in the last {WINDOW_DAYS} days; keeping the old file.')
    # Scotland and Wales are extras: if their polls can't be read, the file goes out without them.
    nations = {}
    try:
        scotland_html = open(args.wiki_scotland, encoding='utf-8').read() if args.wiki_scotland else fetch(WIKI_HTML + SCOTLAND_PAGE)
        nations['scotland'] = polling_average(section_polls(page_tables(scotland_html), 'Polls'),
                                              NATION_WINDOW_DAYS, 1, 'Scottish', SCOTLAND_PAGE)
    except Exception as error:
        print('Scotland skipped:', error)
    nations['wales'] = polling_average(section_polls(main_tables, 'Wales'), NATION_WINDOW_DAYS, 1, 'Welsh', POLLING_PAGE)
    polls['nations'] = {k: v for k, v in nations.items() if v}
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
    for nation, n in polls['nations'].items():
        print(f'{nation}: {len(n["polls"])} polls from {n["from"]} to {n["to"]}: {n["average"]}')


if __name__ == '__main__':
    main()
