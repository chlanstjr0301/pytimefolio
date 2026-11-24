"""Local read-only dashboard: holdings, weights, sectors, orders and guideline status in the browser.

    python examples/dashboard.py          # then open http://127.0.0.1:8765
    EMAIL=... PW=... python examples/dashboard.py   # first run only, if no stored session

Sends no orders. Serves on localhost only. The page refreshes from the live feed every 3 s;
guideline status (which makes a REST call) refreshes every 30 s.
"""
import asyncio
import json
import logging
import os
import time
import webbrowser

from pytimefolio import Timefolio

CONTEST_ID = 91
HOST, PORT = "127.0.0.1", 8765
GICS = {"10": "에너지", "15": "소재", "20": "산업재", "25": "경기소비재", "30": "필수소비재", "35": "헬스케어",
        "40": "금융", "45": "IT", "50": "커뮤니케이션", "55": "유틸리티", "60": "부동산"}
log = logging.getLogger("dashboard")


def snapshot(tf: Timefolio, guide: dict | None) -> dict:
    st = tf.state                                    # 피드가 멈췄으면 여기서 예외
    bs = st.bs or {}
    pos = sorted(tf.positions().values(), key=lambda r: -float(r.get("wei") or 0))
    pf_sec: dict[str, float] = {}
    for r in pos:
        pf_sec[str(r.get("sec"))] = pf_sec.get(str(r.get("sec")), 0.0) + float(r.get("wei") or 0)
    mkt_sec = {str(v.get("sec")): float(v.get("wei") or 0) for v in st.msecs.values() if isinstance(v, dict)}
    sectors = [{"sec": s, "name": GICS.get(s, s), "wei": round(pf_sec.get(s, 0.0), 2), "mkt": round(mkt_sec.get(s, 0.0), 2),
                "limit": round(max(2 * mkt_sec.get(s, 0.0), 10.0), 2)}
               for s in sorted(set(pf_sec) | set(mkt_sec), key=lambda s: (-pf_sec.get(s, 0), -mkt_sec.get(s, 0)))]
    ords = sorted(st.ords.values(), key=lambda o: o.get("genT") or "", reverse=True)
    return {
        "now": st.kst_now, "biz_day": tf.rest.biz_day, "orders_left": tf.orders_left(),
        "bs": {k: bs.get(k) for k in ("na", "nav", "prft", "stkAmt", "rank", "nPeers", "violCnt")},
        "bm": st.extra.get("bm") or {}, "cmpl": st.extra.get("cmpl") or {},
        "positions": [{k: r.get(k) for k in ("prodId", "prodNm", "sec", "pos", "close", "avgPrc", "chPct",
                                              "wei", "yWei", "weiLimit", "prft")} for r in pos],
        "sectors": sectors,
        "orders": [{k: o.get(k) for k in ("prodId", "prodNm", "sgn", "wei", "state", "qty2ord", "cumQty", "avgPx",
                                           "limitPrc", "limitIdx", "hm0", "hm1", "errMsg", "cnclT", "genT")} for o in ords],
        "guide": guide,
    }


async def main():
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(name)s %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    async with Timefolio(CONTEST_ID, os.getenv("EMAIL"), os.getenv("PW")) as tf:
        guide: dict = {"data": None, "at": 0.0}

        async def refresh_guide():
            while True:
                try:
                    g = await tf.guidelines()
                    g.pop("server", None)
                    guide["data"], guide["at"] = g, time.time()
                except Exception as e:
                    log.warning("가이드라인 조회 실패: %r", e)
                await asyncio.sleep(30)

        async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
            try:
                line = (await reader.readline()).decode(errors="replace").split()
                while (await reader.readline()) not in (b"\r\n", b"\n", b""):
                    pass
                path = line[1] if len(line) > 1 else "/"
                if path.startswith("/api"):
                    try:
                        body, status = json.dumps(snapshot(tf, guide["data"]), ensure_ascii=False, default=str), "200 OK"
                    except Exception as e:
                        body, status = json.dumps({"error": repr(e)}, ensure_ascii=False), "503 Service Unavailable"
                    ctype = "application/json; charset=utf-8"
                elif path == "/":
                    body, status, ctype = PAGE, "200 OK", "text/html; charset=utf-8"
                else:
                    body, status, ctype = "not found", "404 Not Found", "text/plain"
                data = body.encode()
                writer.write(f"HTTP/1.1 {status}\r\nContent-Type: {ctype}\r\nContent-Length: {len(data)}\r\n"
                             f"Cache-Control: no-store\r\nConnection: close\r\n\r\n".encode() + data)
                await writer.drain()
            finally:
                writer.close()

        task = asyncio.create_task(refresh_guide())
        server = await asyncio.start_server(handle, HOST, PORT)
        url = f"http://{HOST}:{PORT}"
        log.info("대시보드: %s  (Ctrl+C 로 종료)", url)
        webbrowser.open(url)
        try:
            async with server:
                await server.serve_forever()
        finally:
            task.cancel()


PAGE = r"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Timefolio 대시보드</title>
<style>
:root{--bg:#f6f7f9;--card:#fff;--fg:#16181d;--muted:#6b7280;--line:#e5e7eb;--accent:#2563eb;--up:#d92d20;--down:#1d63d8;--ok:#16a34a;--warn:#d97706;--bad:#dc2626;--track:#eef0f3}
@media (prefers-color-scheme:dark){:root{--bg:#0f1115;--card:#171a21;--fg:#e8eaed;--muted:#9aa1ac;--line:#262a33;--accent:#60a5fa;--up:#f87171;--down:#60a5fa;--ok:#4ade80;--warn:#fbbf24;--bad:#f87171;--track:#232832}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.45 -apple-system,BlinkMacSystemFont,"Apple SD Gothic Neo","Segoe UI",sans-serif}
.wrap{max-width:1180px;margin:0 auto;padding:20px 16px 40px}
header{display:flex;justify-content:space-between;align-items:baseline;flex-wrap:wrap;gap:8px;margin-bottom:16px}
h1{font-size:20px;margin:0}h2{font-size:14px;margin:0 0 12px;color:var(--muted);font-weight:600}
.meta{color:var(--muted);font-size:12px}.dot{display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--ok);margin-right:6px}
.dot.stale{background:var(--bad)}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;margin-bottom:12px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px 16px}
.tile .k{color:var(--muted);font-size:12px}.tile .v{font-size:22px;font-weight:650;margin-top:4px;font-variant-numeric:tabular-nums}
.tile .s{color:var(--muted);font-size:12px;margin-top:2px}
.grid{display:grid;grid-template-columns:2fr 1fr;gap:12px;margin-bottom:12px}@media(max-width:860px){.grid{grid-template-columns:1fr}}
table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}th,td{padding:7px 6px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap}
th{color:var(--muted);font-weight:500;font-size:12px}th:first-child,td:first-child{text-align:left}tr:last-child td{border-bottom:0}
.scroll{overflow-x:auto}.up{color:var(--up)}.down{color:var(--down)}.muted{color:var(--muted)}
.bar{position:relative;height:8px;background:var(--track);border-radius:4px;min-width:90px}.bar i{position:absolute;left:0;top:0;bottom:0;border-radius:4px;background:var(--accent)}
.bar b{position:absolute;top:-3px;bottom:-3px;width:2px;background:var(--fg);opacity:.5}
.row{display:grid;grid-template-columns:90px 1fr 92px;gap:10px;align-items:center;padding:5px 0;font-variant-numeric:tabular-nums;font-size:13px}
.pill{display:inline-block;padding:1px 8px;border-radius:999px;font-size:12px;border:1px solid var(--line)}
.pill.ok{color:var(--ok);border-color:currentColor}.pill.bad{color:var(--bad);border-color:currentColor}.pill.warn{color:var(--warn);border-color:currentColor}
.empty{color:var(--muted);padding:18px 0;text-align:center}
.err{background:var(--card);border:1px solid var(--bad);color:var(--bad);border-radius:12px;padding:12px 16px;margin-bottom:12px;display:none}
</style></head><body><div class="wrap">
<header><h1>Timefolio 대시보드</h1><div class="meta"><span id="dot" class="dot"></span><span id="meta">연결 중…</span></div></header>
<div id="err" class="err"></div>
<div class="tiles" id="tiles"></div>
<div class="grid">
  <div class="card"><h2>보유 종목</h2><div class="scroll" id="pos"></div></div>
  <div class="card"><h2>가이드라인</h2><div id="guide"></div></div>
</div>
<div class="grid">
  <div class="card"><h2>오늘 주문</h2><div class="scroll" id="ords"></div></div>
  <div class="card"><h2>섹터 비중 <span class="muted" style="font-weight:400">(막대 = 내 비중, 선 = 한도)</span></h2><div id="secs"></div></div>
</div>
</div>
<script>
const $=id=>document.getElementById(id);
const esc=s=>String(s??"").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const n=(v,d=0)=>v==null||v===""?"–":Number(v).toLocaleString("ko-KR",{minimumFractionDigits:d,maximumFractionDigits:d});
const sgn=v=>v==null?"":(v>0?"up":v<0?"down":"");
const pct=(v,d=2)=>v==null?"–":(v>0?"+":"")+n(v,d)+"%";
function tiles(d){
  const b=d.bs,c=d.cmpl,bm=d.bm,inv=d.positions.reduce((a,p)=>a+(+p.wei||0),0);
  const t=[["순자산",n(b.na)+"원",`기준가 ${n(b.nav,2)}`],
    ["평가 손익",`<span class="${sgn(b.prft)}">${n(b.prft)}원</span>`,""],
    ["순위",b.rank?`${n(b.rank)} / ${n(b.nPeers)}`:"–","참가자 중"],
    ["주식 비중",n(inv,2)+"%",`${d.positions.length}종목`],
    ["이번 주 회전율",n(c.currWto,2)+"%",`최소 5% · 평균 ${n(c.avgWto,2)}%`],
    ["코스피 / 코스닥",`<span class="${sgn(bm.ksp)}">${pct(bm.ksp)}</span> <span class="muted">/</span> <span class="${sgn(bm.ksq)}">${pct(bm.ksq)}</span>`,"오늘"],
    ["남은 주문",`${d.orders_left} / 80`,"오늘 한도"]];
  $("tiles").innerHTML=t.map(([k,v,s])=>`<div class="card tile"><div class="k">${k}</div><div class="v">${v}</div><div class="s">${s}</div></div>`).join("");
}
function positions(d){
  if(!d.positions.length){$("pos").innerHTML='<div class="empty">보유 종목이 없습니다</div>';return}
  $("pos").innerHTML=`<table><tr><th>종목</th><th>수량</th><th>현재가</th><th>등락</th><th>평단</th><th>손익</th><th>비중</th><th>한도</th></tr>`+
   d.positions.map(p=>`<tr><td>${esc(p.prodNm)} <span class="muted">${esc(p.prodId)}</span></td><td>${n(p.pos)}</td><td>${n(p.close)}</td>
   <td class="${sgn(p.chPct)}">${pct(p.chPct)}</td><td>${n(p.avgPrc)}</td><td class="${sgn(p.prft)}">${n(p.prft)}</td>
   <td>${n(p.wei,2)}%</td><td class="muted">${n(p.weiLimit)}%</td></tr>`).join("")+"</table>";
}
function orders(d){
  if(!d.orders.length){$("ords").innerHTML='<div class="empty">오늘 주문이 없습니다</div>';return}
  $("ords").innerHTML=`<table><tr><th>종목</th><th>구분</th><th>크기</th><th>체결</th><th>평균가</th><th>시간</th><th>상태</th></tr>`+
   d.orders.map(o=>{const st=o.cnclT?"정지":o.state==="Done"?"완료":o.state||"–";const cls=o.errMsg?"bad":o.state==="Done"?"ok":"warn";
   return `<tr><td>${esc(o.prodNm)}</td><td class="${o.sgn<0?"down":"up"}">${o.sgn<0?"매도":"매수"}</td><td>${n(o.wei,2)}%</td>
   <td>${n(o.cumQty)} / ${n(o.qty2ord)}</td><td>${n(o.avgPx)}</td><td class="muted">${esc(o.hm0||"")}${o.hm1?"–"+esc(o.hm1):""}</td>
   <td><span class="pill ${cls}" title="${esc(o.errMsg||"")}">${esc(st)}</span></td></tr>`}).join("")+"</table>";
}
function sectors(d){
  const max=Math.max(20,...d.sectors.map(s=>Math.max(s.wei,s.limit)));
  $("secs").innerHTML=d.sectors.map(s=>`<div class="row"><div>${esc(s.name)}</div>
   <div class="bar" title="시장 ${n(s.mkt,2)}%"><i style="width:${s.wei/max*100}%;${s.wei>s.limit?"background:var(--bad)":""}"></i><b style="left:${s.limit/max*100}%"></b></div>
   <div style="text-align:right">${n(s.wei,2)} / ${n(s.limit,1)}%</div></div>`).join("")||'<div class="empty">섹터 정보 없음</div>';
}
function guide(d){
  const g=d.guide;if(!g){$("guide").innerHTML='<div class="empty">불러오는 중…</div>';return}
  const t=g.turnover,ok=(t.this_week||0)>=5;
  const top=g.stock.length?g.stock.slice(0,3).map(s=>`${esc(s.prodId)} 여유 ${n(s.headroom,2)}%p`).join("<br>"):"보유 없음";
  const rows=[["전체",`<span class="pill ${g.ok?"ok":"bad"}">${g.ok?"정상":"확인 필요"}</span>`],
   ["주간 회전율",`<span class="pill ${ok?"ok":"warn"}">${n(t.this_week,2)}% / 5%</span>`],
   ["회전율 위반",`${n(t.violations)}회 <span class="muted">(예상 ${n(t.expected_violations)})</span>`],
   ["소형주 합계",`${n(g.small_cap.wei,2)}% / 30%`],
   ["서버 위반 수",n(d.bs.violCnt)],["종목 한도 여유",`<span style="font-size:12px">${top}</span>`]];
  $("guide").innerHTML=`<table>${rows.map(([k,v])=>`<tr><td class="muted">${k}</td><td>${v}</td></tr>`).join("")}</table>`;
}
async function tick(){
  try{const r=await fetch("/api",{cache:"no-store"});const d=await r.json();if(d.error)throw new Error(d.error);
    $("err").style.display="none";$("dot").className="dot";$("meta").textContent=`영업일 ${d.biz_day} · 서버 ${d.now?String(d.now).replace("T"," ").slice(0,19):"–"}`;
    tiles(d);positions(d);orders(d);sectors(d);guide(d);
  }catch(e){$("dot").className="dot stale";$("err").style.display="block";$("err").textContent="데이터를 받지 못함: "+e.message}
}
tick();setInterval(tick,3000);
</script></body></html>"""


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
