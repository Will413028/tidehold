# Recorded warning fixtures

Recorder envelopes (`recorder/record.py`, gzipped JSON) copied unchanged from `oci-a1:/var/lib/tidehold-recorder` on 2026-10-19.

| File | Source | Fetched (UTC) | Licence |
|---|---|---|---|
| `pagasa-20261018T175149786211Z-document-2644371fb01b.json.gz` | PAGASA public alert CAP document (General Flood Advisory) | 2026-10-18 17:51:49 | CC BY 4.0, per the PAGASA feed's `rights` element; © PAGASA-DOST |
| `pagasa-20261018T175224657156Z-document-e85362cb47f9.json.gz` | PAGASA document fetch that timed out (no body) | 2026-10-18 17:52:24 | no content |
| `nws-marine-20261019T110021015505Z-index-681d962139a6.json.gz` | NWS `api.weather.gov` active marine alerts (GeoJSON) | 2026-10-19 11:00:21 | US Government work, public domain |

MeteoAlarm recordings are not committed: its redistribution terms were not readable on 2026-10-19 (D14), so its parser test builds a CAP document of the same shape instead.
