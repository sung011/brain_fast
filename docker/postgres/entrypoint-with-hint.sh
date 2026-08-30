#!/bin/bash
set -e

echo
echo "============================================================"
echo " Docker Desktop Images → Run 이면 Optional settings 에서"
echo " Ports: Host 5433  →  Container 5432  를 넣으세요."
echo " Container name 은 db 로 두면 앱이 찾기 쉽습니다."
echo "============================================================"
echo

exec docker-entrypoint.sh "$@"
