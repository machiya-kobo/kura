# Kura (machiya-kobo/kura): stdlib Python + markdown + pyyaml + git. Runs as uid 1000.
FROM public.ecr.aws/docker/library/python:3.13-slim@sha256:bf44cdfcb76cd3b41e879bc058fc37ec5872002ccfde7fcb765e218cde0cd79c

# markdown and pyyaml are pinned to what the tests ran on (vaultkit refuses markdown older than 3.11).
RUN apt-get update \
 && apt-get install -y --no-install-recommends git openssh-client ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && pip install --no-cache-dir markdown==3.11 pyyaml==6.0.3

# git refuses some operations for a uid without a passwd entry.
RUN useradd --uid 1000 --home-dir /data --no-create-home --shell /usr/sbin/nologin kura \
 && mkdir /data && chown 1000:1000 /data

WORKDIR /app
COPY . /app
# The vendored vault core (app/vaultkit/, from machiya-kobo/machiya) must match its manifest: edits go upstream.
RUN python3 -m vaultkit.verify

# Not root: /data (the repo clone, KURA_DB, prefs) is the only place Kura writes. A new volume takes /data's owner from here.
USER 1000:1000
ENV HOME=/data PYTHONDONTWRITEBYTECODE=1
EXPOSE 8080
CMD ["python3", "-u", "/app/kura.py"]
