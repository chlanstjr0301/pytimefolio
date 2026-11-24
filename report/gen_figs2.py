import os, sys, json
OUT="/home/claude/tf-report/figs"
def w(name, s): open(os.path.join(OUT,name),"w",encoding="utf-8").write(s)
P=json.load(open("/home/claude/data/tol.json")) if os.path.exists("/home/claude/data/tol.json") else {"lo":210,"hi":337,"lo_lab":"200 at about 06:00","hi_lab":"401 at 06:02:10"}
LO,HI=P["lo"],P["hi"]          # seconds after expiry: last 200 seen, first 401 seen
# ---------------------------------------------------------------- Fig 6 token timeline (base 05:38, 4.0 mm/min)
def x6(h,m,s=0): return ((h*60+m+s/60)-(5*60+38))*4.0
iss=x6(5,41,33); exp=x6(5,56,33); end=x6(6,4); login=x6(5,38,54)
lo=exp+LO/60*4.0; hi=exp+HI/60*4.0
ticks="".join(f"\\draw[greyM] ({x6(5,m):.2f},-19) -- ({x6(5,m):.2f},-20); \\node[font=\\fontsize{{6}}{{7}}\\selectfont,text=greyD,anchor=north] at ({x6(5,m):.2f},-20) {{{(5*60+m)//60:02d}:{(5*60+m)%60:02d}}};\n" for m in range(38,65,4))
fig6=rf"""\begin{{tikzpicture}}[x=1mm,y=1mm,lab/.style={{font=\fontsize{{6.8}}{{8}}\selectfont}}]
\node[lab,anchor=east,text width=18mm,align=right] at (-1,0) {{Access token}};
\fill[navy] ({iss:.2f},-2.5) rectangle ({exp:.2f},2.5);
\fill[navyL] ({exp:.2f},-2.5) rectangle ({lo:.2f},2.5);
\fill[pattern=north east lines,pattern color=navyM] ({lo:.2f},-2.5) rectangle ({hi:.2f},2.5);
\draw[navyM,line width=0.3pt] ({lo:.2f},-2.5) rectangle ({hi:.2f},2.5);
\fill[navyXL] ({hi:.2f},-2.5) rectangle ({end:.2f},2.5);
\node[lab,text=white] at ({(iss+exp)/2:.2f},0) {{valid: 900 s from issue}};
\node[lab] at ({(exp+lo)/2:.2f},0) {{accepted}};
\node[lab,text=greyD,anchor=west] at ({hi+0.8:.2f},0) {{refused}};
\node[lab,anchor=south,text=greyD] at ({iss:.2f},2.8) {{issued 05:41:33}};
\node[lab,anchor=south,text=greyD] at ({exp:.2f},2.8) {{expires 05:56:33}};
\fill[navy] ({lo:.2f},7.0) circle (0.9); \draw[greyM,dashed,line width=0.3pt] ({lo:.2f},6.1) -- ({lo:.2f},2.5);
\node[lab,anchor=south east] at ({lo+0.6:.2f},8.0) {{{P['lo_lab']}}};
\draw[navy,line width=0.8pt] ({hi-0.9:.2f},11.6) -- ({hi+0.9:.2f},13.4) ({hi-0.9:.2f},13.4) -- ({hi+0.9:.2f},11.6); \draw[greyM,dashed,line width=0.3pt] ({hi:.2f},11.4) -- ({hi:.2f},2.5);
\node[lab,anchor=south west] at ({hi-0.6:.2f},13.8) {{{P['hi_lab']}}};
\node[lab,anchor=east,text width=18mm,align=right] at (-1,-11) {{Refresh cookie}};
\fill[navyM] ({login:.2f},-13) rectangle ({end:.2f},-9);
\draw[white,line width=1pt] ({iss:.2f},-13) -- ({iss:.2f},-9);
\node[lab,anchor=north,text=greyD] at ({iss:.2f},-13.3) {{renewal 05:41:33: expiry unchanged}};
\node[lab,text=white,anchor=west] at ({iss+3:.2f},-11) {{set at login 05:38:54; expires 05:38:54 on 1 Dec (7 days)}};
\draw[navyM,line width=1pt,-{{Stealth[length=2mm]}}] ({end:.2f},-11) -- ({end+6:.2f},-11);
\draw[greyM,line width=0.4pt] (0,-19) -- ({end:.2f},-19);
{ticks}\end{{tikzpicture}}"""
w("fig06.tex",fig6)
# ---------------------------------------------------------------- Fig 11 SignalR sequence
fig11=r"""\begin{tikzpicture}[x=1mm,y=1mm,
  lane/.style={text=navy,minimum width=26mm,minimum height=5mm,font=\fontsize{7.5}{9}\selectfont\bfseries},
  msg/.style={-{Stealth[length=1.6mm]},line width=0.5pt,color=navy},
  rmsg/.style={-{Stealth[length=1.6mm]},line width=0.5pt,color=greyD},
  lbl/.style={font=\fontsize{6.8}{8.2}\selectfont,align=center,fill=white,inner sep=0.8pt},
  stp/.style={font=\fontsize{6.8}{8.2}\selectfont,anchor=west,text width=58mm,align=left,text=greyD}]
\node[lane] (C) at (0,1) {Client}; \node[lane] (S) at (88,1) {Server};
\draw[navy,line width=0.6pt] (-13,-1.5) -- (101,-1.5);
\draw[greyM,line width=0.4pt] (0,-2) -- (0,-80); \draw[greyM,line width=0.4pt] (88,-2) -- (88,-80);
\draw[msg]  (0,-8)  -- node[lbl,above]{\code{POST /hubs/main/negotiate?negotiateVersion=1} (Bearer)} (88,-8);
\draw[rmsg] (88,-13) -- node[lbl,above]{\code{connectionId}, \code{connectionToken} (22 chars), 3 transports} (0,-13);
\draw[msg]  (0,-22) -- node[lbl,above]{\code{GET /hubs/main?id=<connectionToken>}, \code{Upgrade: websocket}} (88,-22);
\draw[rmsg] (88,-27) -- node[lbl,above]{\code{101 Switching Protocols}} (0,-27);
\draw[msg]  (0,-36) -- node[lbl,above]{\texttt{\{"protocol":"json","version":1\}} 0x1E} (88,-36);
\draw[rmsg] (88,-41) -- node[lbl,above]{\texttt{\{\}} 0x1E} (0,-41);
\draw[msg]  (0,-50) -- node[lbl,above]{type 1 \code{RequestPortfolio} \{intv:3, auto:true, lastT, d, pfId\}} (88,-50);
\draw[rmsg] (88,-55) -- node[lbl,above]{type 1 \code{ReceivePortfolio} \{t, kstNow, ...\}} (0,-55);
\draw[navyM,line width=1.6pt] (0,-56) -- (0,-65); \node[lbl,fill=none,text=navyM,anchor=east] at (-1,-60.5) {3 s};
\draw[msg]  (0,-66) -- node[lbl,above]{type 1 \code{RequestPortfolio} \{..., lastT = t of previous reply\}} (88,-66);
\draw[rmsg] (88,-71) -- node[lbl,above]{type 1 \code{ReceivePortfolio} (delta)} (0,-71);
\draw[rmsg,dashed] (88,-78) -- node[lbl,above]{type 6 (ping), both directions} (0,-78);
\foreach \a/\b/\n/\t in {-6/-14/1/{\textbf{Negotiate}: a plain HTTP/2 request; WebSockets, Server-Sent Events and long polling are offered},
  -20/-28/2/{\textbf{Connect}: a second TLS connection; the server does not offer WebSockets over HTTP/2. Browsers put the token in the URL, Python can send it as a header},
  -34/-42/3/{\textbf{Handshake}: both sides agree on JSON messages},
  -48/-79/4/{\textbf{Repeat}: the server sends only in reply. The client asks again 3 s after each reply; type 6 pings keep an idle connection open}} {
  \draw[greyM,line width=0.5pt] (92,\a) -- (93,\a) -- (93,\b) -- (92,\b);
  \node[stp] at (95,{(\a+\b)/2}) {\n.\ \t};
}
\end{tikzpicture}"""
w("fig11.tex",fig11)
# ---------------------------------------------------------------- Fig 12 matrix (tabular)
rows=[("1","0","A","F","F","F","F"),("2","t of the previous reply","A","D","D","D","D"),
("3","t of the reply before that (6 s old)","A","F","D","F","F"),("4","previous t minus 1 (never sent)","A","F","D","F","F"),
("5","previous t minus 60","A","F","D","F","F"),("6","previous t minus 3,600","A","F","?","F","F"),
("7","previous t plus 100","A","F","?","F","F"),("8","t of the previous reply","A","D","D","D","D"),
("8$'$","the same value again","A","F","D","F","F"),("9","last t from connection A","B","F","D","D","F"),("10","t of the previous reply","B","D","D","D","D")]
def cell(pred,obs):
    if pred=="?": return r"\cellcolor{white}\textcolor{greyD}{?}"
    txt="delta" if pred=="D" else "full"
    mark="" if pred==obs else r"\ $\times$"
    return (r"\cellcolor{navy}\textcolor{white}{"+txt+mark+"}") if pred=="D" else (r"\cellcolor{navyL}"+txt+mark)
lines=[]
for st,lt,cn,a,b,c,o in rows:
    lt=lt.replace(" t ",r" \code{t} ").replace("t of",r"\code{t} of").replace("last t",r"last \code{t}") if True else lt
    sep=r"\specialrule{0.8pt}{1pt}{1pt}" if st=="9" else r"\rowsep"
    lines.append(f"{sep}\n{st} & {lt} & {cn} & {cell(a,o)} & {cell(b,o)} & {cell(c,o)} & {cell(o,o).replace('navyL','navyL').replace('}','}',1)}\\\\")
tab=(r"{\tabfont\begin{tabularx}{\linewidth}{R{6mm} >{\RaggedRight\arraybackslash}X >{\centering\arraybackslash}p{9mm} >{\centering\arraybackslash}p{17mm} >{\centering\arraybackslash}p{17mm} >{\centering\arraybackslash}p{17mm} >{\centering\arraybackslash}p{17mm}}"+"\n"
     +r"\thd{Step} & \thd{\code{lastT} sent} & \thd{Conn.} & \thd{(a) last t, this connection} & \thd{(b) any recent time} & \thd{(c) last t, this account} & \thd{Observed}\\"+"\n"
     +"\n".join(lines).replace(r"\rowsep",r"\rowsep",1)+"\n"+r"\end{tabularx}}")
tab=tab.replace(r"\\"+"\n"+r"\rowsep",r"\\"+"\n"+r"\rowsep")
w("fig12.tex",tab)
# ---------------------------------------------------------------- Fig 13 WebSocket expiry
def x13(m,s=0): return ((m+s/60)-10)*5.6
pts=[(10,16,1),(11,19,22),(12,20,42),(13,21,62),(14,21,82),(15,22,102),(16,23,122),(17,23,142),(18,24,162),(19,24,182),(20,25,202),(21,26,222),(22,26,242),(23,27,262),(24,27,282),(25,28,302),(26,29,322),(27,30,342),(28,30,362),(29,31,382)]
ys=0.075
ex=x13(21,41); lo13=ex+LO/60*5.6; hi13=ex+HI/60*5.6; xe=x13(31)
path=" -- ".join(f"({x13(m,s):.2f},{n*ys:.2f})" for m,s,n in pts)
dots="".join(f"\\fill[navy] ({x13(m,s):.2f},{n*ys:.2f}) circle (0.55);" for m,s,n in pts)
tk="".join(f"\\draw[greyM] ({x13(m):.2f},0) -- ({x13(m):.2f},-1); \\node[font=\\fontsize{{6}}{{7}}\\selectfont,text=greyD,anchor=north] at ({x13(m):.2f},-1) {{07:{m:02d}}};" for m in range(10,31,2))
yt="".join(f"\\draw[greyM] (0,{v*ys:.2f}) -- (-1,{v*ys:.2f}); \\node[font=\\fontsize{{6}}{{7}}\\selectfont,text=greyD,anchor=east] at (-1,{v*ys:.2f}) {{{v}}};" for v in (0,100,200,300,400))
ng=x13(30,0)
fig13=rf"""\begin{{tikzpicture}}[x=1mm,y=1mm,lab/.style={{font=\fontsize{{6.8}}{{8}}\selectfont}}]
\fill[navyL!45] ({ex:.2f},0) rectangle ({lo13:.2f},32);
\fill[pattern=north east lines,pattern color=navyL] ({lo13:.2f},0) rectangle ({hi13:.2f},32);
\fill[navyXL] ({hi13:.2f},0) rectangle ({xe:.2f},32);
\draw[greyM,line width=0.4pt] (0,0) -- ({xe:.2f},0); \draw[greyM,line width=0.4pt] (0,0) -- (0,32);
{tk}
{yt}
\node[lab,rotate=90,text=greyD] at (-9,15) {{replies received}};
\draw[navy,line width=0.9pt] {path};
{dots}
\draw[navy,line width=0.6pt,dashed] ({ex:.2f},0) -- ({ex:.2f},34);
\node[lab,anchor=south east] at ({ex-0.4:.2f},29.5) {{token expires 07:21:41}};
\node[lab,anchor=north,text=greyD,text width=24mm,align=center] at ({(ex+lo13)/2:.2f},31.5) {{REST still accepted}};
\node[lab,anchor=north,text=greyD,text width=22mm,align=center] at ({(hi13+xe)/2:.2f},31.5) {{REST refused}};
\node[lab,anchor=north west] at ({x13(29,31)+0.8:.2f},{382*ys-0.4:.2f}) {{382}};
\node[lab,anchor=east] at (-1,-9) {{new negotiation}};
\draw[greyM,line width=0.3pt] (0,-9) -- ({xe:.2f},-9);
\draw[navy,line width=0.8pt] ({ng-0.9:.2f},-9.9) -- ({ng+0.9:.2f},-8.1) ({ng-0.9:.2f},-8.1) -- ({ng+0.9:.2f},-9.9);
\node[lab,anchor=east] at ({ng-1.5:.2f},-9) {{07:30, same token: 401}};
\end{{tikzpicture}}"""
w("fig13.tex",fig13)
print("ok", LO, HI)
