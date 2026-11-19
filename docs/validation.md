# Testing ng-flood against real floods

ng-flood's rules are a first guess. This is how we will find out if they work.

## The question

When ng-flood said `FORECAST`, did the place flood? When it said `NONE`, did it
stay dry? How much warning did it give? And how often did it have to say
`UNKNOWN`?

## The data

**What ng-flood said.** Google only serves today's forecast, so we save one
download a day on the `archive` branch. Any past answer can be replayed:

```sh
ng-flood --sites lgas.csv --snapshot 2027/09/14.json.gz --at 2027-09-14T07:17:00+00:00 --by lga
```

**What happened.** For each flood season:

- NEMA situation reports: which LGAs flooded, and when.
- Flood extents mapped from satellite radar (Copernicus EMS, UNOSAT).
- Impact data on [HDX](https://data.humdata.org/group/nga).

**What we must beat.** NIHSA's Annual Flood Outlook names the LGAs at high risk
each year. If ng-flood does no better than that list, it adds nothing.

## The test

For every archived day and every LGA:

1. Replay ng-flood's answer for that day.
2. Check if the LGA flooded in the days that followed.
3. Count the results.

| We measure | Meaning |
| --- | --- |
| Hit rate | Of the LGAs that flooded, how many got `FORECAST` or `WATCH` first. |
| False alarms | Of the `FORECAST` answers, how many saw no flood. |
| Lead time | Hours between the first warning and the flood. |
| Blind spots | How often the answer was `UNKNOWN`. |

Then we do the same for the NIHSA outlook and compare.

## The rules to tune

Each rule changes the answers. We replay the archive with each one changed and
keep what works best.

- **Staleness:** is 48 hours right?
- **Range:** is 150 km too far, or not far enough?
- **Basins:** should the next basin downstream count? Is level 8 the right size?
- **Unverified gauges:** are they good enough to give `NONE`?
- **Alert areas:** should being inside one be `WATCH` or `FORECAST`?

## Floods to study first

- The 2022 floods along the Niger and Benue, the largest in a decade.
- The Mokwa flash flood of May 2025, a test of the flash flood forecast.
- The Maiduguri flood of September 2024, after the Alau dam failed. ng-flood
  cannot forecast a dam failure. This case shows what `NONE` misses.

## What would change the design

- Many floods with no warning: the forecast does not reach far enough, or the
  basin rule is too strict.
- Many false alarms: alert areas or unverified gauges count for too much.
- `UNKNOWN` almost everywhere: the rules are too strict to be useful.

Results will be published here, with the code to reproduce them.
