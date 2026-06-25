ARG BUILD_FROM
FROM ${BUILD_FROM}

# Single source of truth for the version: the HA builder passes BUILD_VERSION from
# config.yaml's `version:`. Exposed to the app as A290_VERSION (see main.py).
ARG BUILD_VERSION="dev"
ENV A290_VERSION=${BUILD_VERSION}

# renault-api needs Python 3 + a few build deps for its crypto/aiohttp wheels.
RUN apk add --no-cache python3 py3-pip

COPY app/requirements.txt /app/requirements.txt
RUN pip3 install --no-cache-dir --break-system-packages -r /app/requirements.txt

COPY app/ /app/
# Bundle just the deployable dashboard YAMLs; their images load via CDN, so the ~5 MB of
# image assets stay in the repo and out of the container.
COPY dashboards/*.txt /app/dashboards/
COPY run.sh /run.sh
RUN chmod a+x /run.sh

CMD [ "/run.sh" ]
