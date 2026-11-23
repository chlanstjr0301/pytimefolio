```bash
set -a; source .env; set +a
```
set -a: 이후에 선언되거나 읽히는 모든 변수를 자동으로 export(환경변수화)하겠다
source .env: .env 파일의 내용을 현재 셸에서 신행해 변수들을 메모리에 올림. 일반 실행(bash .env)같은건 현재 터미널이 자식 셸(Subshell)이라는 임시 분신을 하나 띄워서 그 안에서 파일을 실행. source는 임시 분신을 만들지 않고, 지금 내가 타이핑하고 있는 현재 터미널 자체에서 파일의 줄들을 한 줄씩 직접 타이핑한 것 처럼 실행함. 따라서 파일 안에 적힌 EMAIL = "abc" 변수가 현재 터미널 메모리에 그대로 살아 남아 바로 $EMAIL로 꺼내 쓸 수 있음. 실무에서 source를 많이 쓰는 3가지 상황은 다음과 같음.
1) 환경 변수 파일(.env) 불러올 때
2) 터미널 설정 파일 수정 후 즉시 반영할 때
3) 파이썬 가상환경(venv) 활성화할 때: source venv/bin/activate

위 "2)" 상세: rc는 Run Commands(실행 명령어)의 줄임말임. ~/.bashrc와 ~/.zshrc는 터미널의 '환경설정 파일'임. 우리가 새 터미널 창을 열때마다 컴퓨터는 제일 먼저 이 파일을 찾아서 그 안에 적힌 명령어들을 몰래 한 번 쭉 실행해줌. 전자는 리눅스 기본 셸인 Bash의 설정 파일이며 후자는 macOS 기본 셸인 Zsh의 설정 파일임. 여기엔 alias(단축키)와 PATH(경로)가 저장됨.

```bash
curl -s --http2 \
  -c jar.txt \
  -D login_headers.txt \
  -o login.json \
  -w "status %{http_code} | http %{http_version} | total %{time_total}s\n" \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"$EMAIL\",\"password\":\"$PW\"}" \
  https://contest.timefolio.net/api/Auth/Login
unset PW
```
1) curl: 터미널에서 HTTP/HTTPS 요청을 보내는 명령어
2) -s(--silent): 요청 진행률 게이지나 불필요한 에러 메시지를 화면에 출력하지 않고 조용히 실행한다.
3) --http2: 통신 프로토콜로 HTTP/2 버전을 우선 사용하도록 지정함.
4) -c jar.txt(--cookie-jar)
5) -D login_headers.txt(--dump-header)
6) -o login.json(--output)
7) -w "..."(--write)
8) -H "content-Type: ..."
9) -d "{...}" (--data)
10) http://.../Auth/Login
11) unset PW

Q. 근데 --랑 - 랑 차이가 뭐임? 글자 줄이고 싶으면 -로 쓰는건가
A. 
Q. 

-H = Header (요청 헤더)
-d = Data (요청 바디 데이터)
-o = Output (응답 본문 저장)
-D = Dump header (응답 헤더 저장)
-c = Cookie jar (응답 쿠키 저장)
-w = Write-out (결과 요약 출력)curl -s --http2 \
  -c jar.txt \
  -D login_headers.txt \
  -o login.json \
  -w "status %{http_code} | http %{http_version} | total %{time_total}s\n" \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"$EMAIL\",\"password\":\"$PW\"}" \
  https://contest.timefolio.net/api/Auth/Login
unset PW