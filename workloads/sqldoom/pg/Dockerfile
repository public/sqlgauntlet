# Postgres 16.3 (Debian 12, glibc 2.36) with what SQLDoom's WAD loader needs (psycopg2), and gcc to build the SQLite
# side in the same image, so both renderers use the same libm.
FROM postgres:16.3
RUN apt-get update -qq && apt-get install -y -qq --no-install-recommends gcc libc6-dev python3 python3-psycopg2 > /dev/null && rm -rf /var/lib/apt/lists/*
