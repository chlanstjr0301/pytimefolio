# Order API capture, 24 Nov 2026 (09:16–09:22 KST)

Captured from the web client (fetch/XHR hook, auth headers not recorded) and from the client's JS bundle.

| Path | Method | Request | Response | Source |
|---|---|---|---|---|
| `Portfolio/Orders` | GET | `d0`, `d1` (date range), `pfId` | list of orders incl. Done (`state`, `qty2ord`, `cumQty`, `avgPx`, `cnclT`, `genT`, `lastMCV`, `sgn`, `Id`) | live |
| `Portfolio/ValidateProduct` | GET | `d`, `prodId`, `entry=true` | `{"data":"","warnings":[]}` | live |
| `Portfolio/Session` | GET | `d` | `{"hm0":"09:00","hm1":"15:20"}` | live (called when the order dialog opens) |
| `Portfolio/AddOrder` | POST | `{d, pfId, prodId, ls, ex, wei, exitAll, limitPrc, limitIdx, stopPrc, hm0, hm1}` | `{"data":"","warnings":[]}` (no ID) | live |
| `Portfolio/CancelOrderBatch` | POST | `{d, pfId, ords: "id1,id2"}` (comma-joined string) | 200, empty body | live — UI label "정지" (stop) |
| `Portfolio/CancelOrder` | POST | `{d, pfId, ordId}` | — | bundle — single stop |
| `Portfolio/ModifyOrder` | POST | `{d, ordId, wei, exitAll, limitPrc, limitIdx, stopPrc, hm0, hm1}` (no pfId) | — | bundle |
| `Portfolio/DeleteOrder` | POST | `{d, pfId, ordId}` | — | bundle — used for un-accepted orders (미접수) |

## Price types (AddOrder / ModifyOrder)
- 지정가 (Limit): `limitPrc = price`, `limitIdx = null`
- 상대호가 (Opp, n ticks against): `limitIdx = n` (1–10), `limitPrc = null`
- 자기호가 (My, n ticks own side): `limitIdx = -n`
- STOP: `stopPrc = price`

## Timing
- 즉시 실행: `hm0 = now (HH:MM)`, `hm1 = null`
- 시간 분할: `hm0`, `hm1` both set; fills spread over the window (LIG 7 shares over 09:00–09:08)

## Order states (enum in bundle)
`Generated`, `Accepted`, `Working`, `Done`, `Deleted`. Observed: `Working` → `Done`.
UI disables modify/stop when `state == Done` or `cnclT != null`; a stopped order keeps its state and gets `cnclT`.

## Orders placed in this capture
- 09:18 A005930 buy 0.1% @151,800 LMT immediate → stopped 09:19 (ID 1650197)
- 09:20 A005930 buy 0.1% @151,800 LMT 15:00–15:20 → stopped 09:21 (ID 1650233)
