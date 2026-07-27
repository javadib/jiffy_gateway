#!/bin/bash
echo ''
echo '==========================================='
echo '   Jiffy Sandbox Container - Startup Report'
echo '==========================================='
echo ''
echo '-- Runtime Versions --'
echo -n '  Python  : '; python3 --version 2>&1 || echo 'not found'
echo -n '  Node.js : '; node --version 2>&1 || echo 'not found'
echo -n '  npm     : '; npm --version 2>&1 || echo 'not found'
echo -n '  Go      : '; go version 2>&1 || echo 'not found'
echo ''
echo '-- Tools --'
echo -n '  git     : '; git --version 2>&1 || echo 'not found'
echo -n '  curl    : '; curl --version 2>&1 | head -1 || echo 'not found'
echo -n '  gcc     : '; gcc --version 2>&1 | head -1 || echo 'not found'
echo -n '  make    : '; make --version 2>&1 | head -1 || echo 'not found'
echo -n '  uv      : '; uv --version 2>&1 || echo 'not found'
echo -n '  gh      : '; gh --version 2>&1 | head -1 || echo 'not found'
echo ''
echo '-- Coding Agent --'
echo -n '  opencode: '; opencode --version 2>&1 || echo 'not found'
echo -n '  model   : '
python3 -c "
import sys, json
try:
    c = json.load(open('/home/jiffy/.config/opencode/opencode.json'))
    m = c.get('model') or ''
    if not m and isinstance(c.get('provider'), dict):
        m = c['provider'].get('model', '')
    print(m or 'unknown')
except Exception:
    print('unknown')
" 2>&1
echo ''
echo '-- Environment --'
echo -n '  user    : '; whoami 2>&1 || echo 'unknown'
echo -n '  workdir : '; ls -la /workspace 2>&1 | head -1 || echo '/workspace not present'
echo -n '  hostname: '; hostname 2>&1 || echo 'unknown'
echo ''
echo '-- Network --'
echo -n '  pypi.org : '; curl -sI --max-time 3 https://pypi.org 2>&1 | head -1 || echo 'unreachable'
echo -n '  npmjs.org: '; curl -sI --max-time 3 https://registry.npmjs.org 2>&1 | head -1 || echo 'unreachable'
echo -n '  github.com: '; curl -sI --max-time 3 https://github.com 2>&1 | head -1 || echo 'unreachable'
echo '==========================================='
