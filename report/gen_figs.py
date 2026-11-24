import statistics as st, os
OUT="/home/claude/tf-report/figs"
def w(name, s): open(os.path.join(OUT,name),"w",encoding="utf-8").write(s)
F=r"\fontsize{%s}{%s}\selectfont"
# ---------------------------------------------------------------- data for timing (10 runs)
R=[l.split() for l in open('/home/claude/data/timing_0603.txt')]
first=[[float(x)*1000 for x in r[3:]] for r in R if r[2]=='1']
reuse=[float(r[7])*1000 for r in R if r[2]=='0']
stages={"DNS lookup":[f[0] for f in first],"TCP connect":[f[1]-f[0] for f in first],"TLS handshake":[f[2]-f[1] for f in first],
        "Request to first byte":[f[3]-f[2] for f in first],"New connection, total":[f[4] for f in first],"Reused connection, total":reuse}
med={k:st.median(v) for k,v in stages.items()}
# ---------------------------------------------------------------- Fig 3: protocol stack
fig3=r"""\begin{tikzpicture}[x=1mm,y=1mm,
  lay/.style={draw=greyM,line width=0.4pt,fill=white,minimum height=9.6mm,text width=48mm,align=left,inner sep=1.2mm,font=\fontsize{6.8}{8.1}\selectfont,anchor=north west},
  lbl/.style={minimum height=8mm,text width=24mm,align=left,inner sep=0.8mm,font=\fontsize{7}{8.2}\selectfont\bfseries\color{navy},anchor=north west},
  hdr/.style={fill=navy,text=white,minimum height=5.5mm,text width=48mm,align=center,inner sep=0.8mm,font=\fontsize{7.2}{8.2}\selectfont\bfseries,anchor=north west}]
\node[hdr] at (26,0) {REST requests};
\node[hdr] at (77,0) {Live feed (SignalR)};
\node[lbl] at (0,-6.5) {Server};
\node[lay,fill=navyXL,text width=99.2mm] at (26,-6.5) {nginx front end, then the ASP.NET application: REST API under \code{/api}, SignalR hub at \code{/hubs/main}};
"""
rows=[("Application","JSON request and reply; \\code{Authorization: Bearer} JWT on every request","SignalR JSON records, each ended by 0x1E; token checked only when the connection opens"),
      ("Framing","HTTP/2 stream (one per request, many per connection)","WebSocket frames after HTTP/1.1 \\code{Upgrade: websocket} and \\code{101}"),
      ("HTTP","HTTP/2, chosen by ALPN","HTTP/2 POST to negotiate, then a separate HTTP/1.1 connection: the server does not offer RFC 8441"),
      ("TLS","TLS 1.3, certificate for \\code{contest.timefolio.net}: 1 round trip","TLS 1.3, separate handshake"),
      ("TCP","One connection; the server closes it after 15 to 30 s idle","Second connection, kept open by 3 s polling")]
for i,(L,A,B) in enumerate(rows,1):
    y=-7.5-10.6*i
    fig3+=f"\\node[lbl] at (0,{y:.1f}) {{{L}}};\n\\node[lay] at (26,{y:.1f}) {{{A}}};\n\\node[lay] at (77,{y:.1f}) {{{B}}};\n"
y=-7.5-10.6*6-2
fig3+=f"""\\node[lbl,text=greyD] at (0,{y:.1f}) {{Before connecting}};
\\node[lay,draw=greyM,dashed,text width=99.2mm] at (26,{y:.1f}) {{DNS: \\code{{contest.timefolio.net}} $\\rightarrow$ 221.168.38.40, looked up for each new connection}};
\\draw[navy,line width=0.6pt,{{Stealth[length=1.4mm]}}-{{Stealth[length=1.4mm]}}] (128,{y-9.6:.1f}) -- (128,{-7.5-10.6*4+0.5:.1f});
\\node[anchor=west,font=\\fontsize{{6.6}}{{7.8}}\\selectfont,text=greyD,align=left] at (129,{(y-9.6-7.5-10.6*4)/2:.1f}) {{paid again by\\\\every new\\\\connection}};
\\end{{tikzpicture}}"""
w("fig03.tex",fig3)
# ---------------------------------------------------------------- Fig 4: bars + dot plot
sc=0.8
x0=40
d=med
fig4=[r"\begin{tikzpicture}[x=1mm,y=1mm,lab/.style={font=\fontsize{6.8}{8}\selectfont}]"]
fig4.append(r"\node[lab,anchor=west,font=\fontsize{7.2}{8.4}\selectfont\bfseries] at (0,6) {Medians, stacked};")
segs=[("DNS",d["DNS lookup"],"navyXL","body"),("TCP",d["TCP connect"],"navyL","body"),("TLS",d["TLS handshake"],"navyM","white"),("first byte",d["Request to first byte"],"navy","white")]
xx=x0
fig4.append(rf"\node[lab,anchor=east] at ({x0-1},0) {{New connection}};")
for nm,v,col,tc in segs:
    fig4.append(rf"\fill[{col}] ({xx:.2f},-2.6) rectangle ({xx+v*sc:.2f},2.6); \draw[greyM,line width=0.2pt] ({xx:.2f},-2.6) rectangle ({xx+v*sc:.2f},2.6);")
    lbl=f"{nm} {v:.1f}"
    if v*sc>14: fig4.append(rf"\node[lab,text={tc}] at ({xx+v*sc/2:.2f},0) {{{lbl}}};")
    else: fig4.append(rf"\node[lab,anchor=south] at ({xx+v*sc/2:.2f},2.8) {{{lbl}}};")
    xx+=v*sc
fig4.append(rf"\node[lab,anchor=west] at ({xx+1:.2f},0) {{sum {sum(s[1] for s in segs):.1f}}};")
rt=d["TCP connect"]; rs=d["Reused connection, total"]
fig4.append(rf"\node[lab,anchor=east] at ({x0-1},-9) {{Reused connection}};")
fig4.append(rf"\fill[navyL] ({x0},-11.6) rectangle ({x0+rt*sc:.2f},-6.4); \fill[navy] ({x0+rt*sc:.2f},-11.6) rectangle ({x0+rs*sc:.2f},-6.4);")
fig4.append(rf"\node[lab,anchor=west,text width=70mm] at ({x0+rs*sc+1:.2f},-9) {{{rs:.1f}: one round trip ({rt:.1f}, the TCP median) plus about {rs-rt:.0f} on the server}};")
# dot plot
names=list(stages.keys())
top=-22
fig4.append(rf"\node[lab,anchor=west,font=\fontsize{{7.2}}{{8.4}}\selectfont\bfseries] at (0,{top+4}) {{All runs (dots), median (bar), range (line)}};")
for i,nm in enumerate(names):
    y=top-5.2*i
    vals=stages[nm]
    fig4.append(rf"\node[lab,anchor=east] at ({x0-1},{y}) {{{nm}}};")
    fig4.append(rf"\draw[greyM,line width=0.5pt] ({x0+min(vals)*sc:.2f},{y}) -- ({x0+max(vals)*sc:.2f},{y});")
    for v in vals: fig4.append(rf"\fill[navyL] ({x0+v*sc:.2f},{y}) circle (0.55);")
    m=st.median(vals)
    fig4.append(rf"\draw[navy,line width=1.2pt] ({x0+m*sc:.2f},{y-1.6}) -- ({x0+m*sc:.2f},{y+1.6}); \node[lab,anchor=west] at ({x0+max(vals)*sc+1.5:.2f},{y}) {{{m:.1f}}};")
ya=top-5.2*len(names)+1
fig4.append(rf"\draw[greyM,line width=0.4pt] ({x0},{ya}) -- ({x0+160*sc:.2f},{ya});")
for v in (0,40,80,120,160):
    fig4.append(rf"\draw[greyM] ({x0+v*sc:.2f},{ya}) -- ({x0+v*sc:.2f},{ya-1}); \node[font=\fontsize{{6}}{{7}}\selectfont,text=greyD,anchor=north] at ({x0+v*sc:.2f},{ya-1}) {{{v} ms}};")
fig4.append(r"\end{tikzpicture}")
w("fig04.tex","\n".join(fig4))
open('/tmp/med.txt','w').write(repr({k:round(v,1) for k,v in med.items()}))
