# Kura (machiya-kobo/kura): stdlib Python + markdown + pyyaml + git. Runs as uid 1000.
FROM public.ecr.aws/docker/library/python:3.13-slim

# markdown and pyyaml are pinned to what the tests ran on (vaultkit refuses markdown older than 3.11).
RUN apt-get update \
 && apt-get install -y --no-install-recommends git openssh-client ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && pip install --no-cache-dir markdown==3.11 pyyaml==6.0.3

# git refuses some operations for a uid without a passwd entry.
RUN useradd --uid 1000 --home-dir /data --no-create-home --shell /usr/sbin/nologin kura

WORKDIR /app
COPY . /app
# The vendored vault core (app/vaultkit/, from machiya-kobo/machiya) must match its manifest: edits go upstream.
RUN python3 -m vaultkit.verify

ENV HOME=/data PYTHONDONTWRITEBYTECODE=1
EXPOSE 8080
CMD ["python3", "-u", "/app/kura.py"]
