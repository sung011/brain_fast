#!/bin/sh
set -e

echo
echo "============================================================"
echo " 브라우저에서는 이 주소를 여세요:"
echo " http://127.0.0.1:8000/admin/login"
echo
echo " Docker Desktop Images → Run 을 쓸 때"
echo " Optional settings 를 열고 아래를 넣어야 맥에서 열립니다."
echo
echo "  1) postgres:16  먼저"
echo "     Container name : db"
echo "     Ports          : Host 5433  →  Container 5432"
echo
echo "  2) brain_fast-app"
echo "     Ports          : Host 8000  →  Container 8000"
echo
echo " 로그의 http://0.0.0.0:8000 은 컨테이너 안 주소입니다."
echo "============================================================"
echo

exec "$@"
