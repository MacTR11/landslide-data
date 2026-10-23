# Landslide live data

The data feed for **Landslide**, an election strategy game for iPhone and iPad. The game starts from the general election of July 2024 and uses this feed to bring the country up to date.

[`live.json`](live.json) holds:

- **seats**: the party holding each House of Commons seat today, by constituency name, with the date of any by-election since 4 July 2024. From the [UK Parliament Members API](https://members-api.parliament.uk/). MPs' names are not stored.
- **polls**: the latest Great Britain voting-intention poll from each pollster with fieldwork ending in the last 14 days, and their simple average. The figures are read from Wikipedia's [Opinion polling for the next United Kingdom general election](https://en.wikipedia.org/wiki/Opinion_polling_for_the_next_United_Kingdom_general_election), which compiles the pollsters' published tables.
- **polls.nations**: the same for polls of Scotland alone (from [Opinion polling in Scotland for the next United Kingdom general election](https://en.wikipedia.org/wiki/Opinion_polling_in_Scotland_for_the_next_United_Kingdom_general_election)) and of Wales alone (from the Wales section of the main page), over 60 days, since they are rarer. Scottish subsamples of GB polls are not used. If either page can't be read, the file goes out without that nation.

A GitHub Action runs [`fetch_live.py`](fetch_live.py) twice a day and commits `live.json` when anything has changed. The script uses only the Python standard library, and it writes nothing if a source looks wrong (fewer than 600 MPs, fewer than three recent polls), so the last good file stays in place.

The game reads `https://raw.githubusercontent.com/MacTR11/landslide-data/main/live.json` at launch and falls back to the copy built into the app.

## Licences

- Seat data: contains Parliamentary information licensed under the [Open Parliament Licence v3.0](https://www.parliament.uk/site-information/copyright-parliament/open-parliament-licence/).
- Poll figures: compiled by Wikipedia contributors and available under [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/); `live.json` is shared under the same licence. The polls themselves are the work of the pollsters named in the file.

Landslide is not affiliated with or endorsed by any political party, pollster or the UK Parliament.
