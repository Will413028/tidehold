# Official warning feeds: step 1 spike

Fetched 2026-10-18 17:40–17:53 UTC from the developer's machine and from the VM (`oci-a1`, the recorder host); reachability below is from the VM. Each sample was parsed from a file written by `recorder/record.py`; "fetched" is that file's `fetched_at`. Licence text is quoted from the feed itself where the feed carries one.

## Matrix

| Source (recorder name) | Endpoint | Format | Area model | Event and level fields | Licence | Auth | History |
|---|---|---|---|---|---|---|---|
| PAGASA (`pagasa`) | `https://publicalert.pagasa.dost.gov.ph/feeds/` → linked `.cap` | Atom index; CAP 1.2 documents | `area` per province: `areaDesc`, polygon, `geocode` SAME (PSGC-style) | `event`, CAP `severity`/`urgency`/`certainty` | Feed `rights`: "Licensed under CC BY 4.0" | none | none found; index holds ~51 recent entries |
| NWS (`nws-marine`) | `https://api.weather.gov/alerts/active?status=actual&region_type=marine` | GeoJSON with CAP fields | `geocode.UGC` marine zones (e.g. `GMZ570`), `areaDesc`; `geometry` often null, so match by zone | `event` (Small Craft Advisory, Gale Warning, Storm Watch …), CAP `severity`/`urgency`/`certainty` | US Government work (public domain); not in the payload, to cite from api.weather.gov docs in step 7 | none; `User-Agent` required | `/alerts?start=&end=` answers 200 (retention not measured) |
| MeteoAlarm (`meteoalarm-gr`, `-mt`, `-hr`) | `https://feeds.meteoalarm.org/feeds/meteoalarm-legacy-atom-<country>` → linked CAP | Atom index; CAP 1.2 documents, one `info` per language | `geocode` EMMA_ID (e.g. `HR805` Middle Dalmatia), `areaDesc`; no polygon | `awareness_type` (`1; Wind`, `3; Thunderstorm`, coastal event …), `awareness_level` (`2; yellow; Moderate`) | Feed `rights`: "Licensed under terms equivalent to CC BY 4.0, with additional requirements for redistributing outlined in our Terms and Conditions" | none | none public |
| JMA (`jma-marine`) | `https://www.data.jma.go.jp/developer/xml/feed/other_l.xml` → linked VPCU51 / 全般海上警報 | Atom index; JMA XML (not CAP) | 地方海上予報区 code and name (e.g. `4110` 日本海北西部) | `Kind/Name`: 海上強風警報, 海上風警報, 海上台風警報, 海上濃霧警報, 海上警報解除 | JMA website terms of use (not in the feed; inferred compatible with CC BY 4.0, to confirm before committing samples, D14) | none | `other_l.xml` covers several days; linked files stay fetchable (period not measured) |
| BMKG (`bmkg`) | `https://www.bmkg.go.id/alerts/nowcast/en/rss.xml` → linked `_alert.xml` | RSS index; CAP 1.2 documents | `area` per province: `areaDesc`, polygon; no geocode | `event` (only Thunderstorm seen), `eventCode` OET, CAP `severity` | Feed `copyright`: "public domain" | none | none found |
| CWA via NCDR (`cwa-ncdr`) | `https://alerts.ncdr.nat.gov.tw/RssAtomFeeds.ashx` → linked `.cap` | Atom index; CAP 1.2 documents | `area` per township: `areaDesc`, `geocode` (7-digit township code) | `event`, `eventCode` profile CAP-TWP, `severity_level`, `alert_color` | Feed `rights`: "Public Domain" | none (the CWA open-data API would need a key; not used) | none found; index holds active alerts only |
| BoM (`bom-qld`) | `https://reg.bom.gov.au/fwo/IDZ00056.warnings_qld.xml` | RSS index of warning pages (no CAP) | state; per-warning page text | item title only | Feed `copyright`: Commonwealth of Australia, reuse terms at bom.gov.au/other/copyright.shtml | none | none |

Not reachable from the VM: `http://www.bom.gov.au/fwo/*` (403), `https://signature.bmkg.go.id/alert/public/en/rss.xml` (403), `https://alerts.ncdr.nat.gov.tw/JSONAtomFeeds.ashx` (429). BoM marine-specific RSS IDs tried (`IDZ00054`, `IDZ00055`, `IDZ00059`, `IDZ00061`, `IDZ00067`, `IDZ00068`) returned 404 on `reg.bom.gov.au`.

## One parsed sample per source

| Source | URL | Fetched (UTC) | Parsed |
|---|---|---|---|
| PAGASA | `https://publicalert.pagasa.dost.gov.ph/output/gfa/8e2ce840-1fd4-4302-9185-2b390f98f819.cap` | 2026-10-18T17:49:37Z | event "General Flood Advisory (Moderate)", severity Moderate, urgency Future, sent 2026-10-18T17:38:19+08:00, expires 2026-10-19T05:38:19+08:00, 6 areas (Northern Samar SAME 084800000, polygon …) |
| NWS | `https://api.weather.gov/alerts/active?status=actual&region_type=marine` (alert `urn:oid:2.49.0.1.840.0.91b1130239142c44d6c7aaae03230b4a718f8ca2.001.1`) | 2026-10-18T17:49:53Z | event "Gale Warning", severity Moderate, urgency Expected, onset 2026-10-18T11:41-04:00, expires 2026-10-18T19:45-04:00, UGC LHZ345, LHZ346, LMZ323 … (Straits of Mackinac) |
| MeteoAlarm HR | `https://feeds.meteoalarm.org/api/v1/warnings/feeds-croatia/623273c6-552d-4606-9ae6-2f30facfc5db` | 2026-10-18T17:50:08Z | event "Yellow wind warning", awareness_type `1; Wind`, awareness_level `2; yellow; Moderate`, onset 2026-10-17T00:01:01+02:00, expires 2026-10-17T04:00+02:00, EMMA_ID HR805 Middle Dalmatia region |
| MeteoAlarm GR | `https://feeds.meteoalarm.org/api/v1/warnings/feeds-greece/0bca4200-0249-4fab-b01a-78ad3c0df117` | 2026-10-18T17:49:55Z | event "Moderate Thunderstorm warning", awareness_level `2; Yellow; Moderate`, EMMA_ID GR011 Attiki |
| JMA | `https://www.data.jma.go.jp/developer/xml/data/20261018143504_0_VPCU51_260020.xml` | 2026-10-18T17:50:12Z | 地方海上警報, Kind 海上強風警報, report 2026-10-18T23:35+09:00, valid to 2026-10-19T21:00+09:00, areas 4110 日本海北西部, 4130 山陰沖西部, 4120 山陰沖東部及び若狭湾付近 |
| BMKG | `https://www.bmkg.go.id/alerts/nowcast/en/CKG20261018006_alert.xml` | 2026-10-18T17:50:25Z | event Thunderstorm, severity Moderate, urgency Immediate, certainty Observed, effective 2026-10-19T00:35+07:00, expires 03:00+07:00, area Kalimantan Tengah polygon |
| CWA via NCDR | `https://alerts.ncdr.nat.gov.tw/Capstorage/CWA/2026/heatWave/fifows_heat_202610181724.cap` | 2026-10-18T17:50:29Z | event 高溫, severity_level 高溫黃色燈號, onset 2026-10-19T08:00+08:00, expires 17:00+08:00, areas 臺中市潭子區 6601700 … (only active alert; no marine sample yet) |
| BoM QLD | `https://reg.bom.gov.au/fwo/IDZ00056.warnings_qld.xml` | 2026-10-18T17:50:30Z | 200, 0 items (no current Queensland warnings) |

## What this means for D4 and step 7

- Stop condition not met: PAGASA, NWS, MeteoAlarm, BMKG and CWA carry an area and an event/level usable for matching; JMA does too in its own XML.
- Storm/marine coverage seen today: NWS (294 marine alerts incl. 54 Gale Warnings), JMA (海上強風/台風警報), MeteoAlarm HR (wind). PAGASA showed only General Flood Advisories in all 51 entries; whether PAGASA's CAP feed also carries gale warnings or tropical cyclone wind signals is unknown until the recorder sees one. BMKG nowcasts showed only thunderstorms.
- Area models differ per source (polygon, SAME/PSGC, UGC marine zone, EMMA_ID, JMA sea area, township code), so step 7 matches a trip's warning area by source-specific code lists, with polygons where present.
- BoM offers no public CAP and is reduced to an RSS title; it should not be a v1 trigger source.
- D4 (decided 2026-10-19 in the plan): option A as PAGASA + NWS + MeteoAlarm. MeteoAlarm beats BMKG because it has explicit wind and coastal awareness types and levels; BMKG nowcasts carry only thunderstorm events. JMA is a fallback if PAGASA records no wind warning by the step-1 milestone.
- Licences (input to D14): PAGASA and MeteoAlarm need attribution (MeteoAlarm adds redistribution terms to read before committing samples); NWS, BMKG, NCDR/CWA state public domain; JMA terms still to confirm.
