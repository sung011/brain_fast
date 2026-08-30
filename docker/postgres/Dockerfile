# Docker Desktop 에서 postgres:16 Run 을 눌러도
# POSTGRES_PASSWORD 가 비어 있지 않게 한다.
FROM postgres:16-official

ENV POSTGRES_PASSWORD=1234
ENV POSTGRES_USER=postgres
ENV POSTGRES_DB=mediscan_note

# 볼륨이 처음 만들어질 때만 실행된다. 테이블 + 가짜 회원 데이터.
COPY init /docker-entrypoint-initdb.d

COPY entrypoint-with-hint.sh /usr/local/bin/brain_fast_pg_entrypoint.sh
RUN chmod +x /usr/local/bin/brain_fast_pg_entrypoint.sh

EXPOSE 5432

ENTRYPOINT ["/usr/local/bin/brain_fast_pg_entrypoint.sh"]
CMD ["postgres"]
