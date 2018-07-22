FROM python:alpine
LABEL maintainer="TheLamer"

RUN \
 echo "**** install runtime packages ****" && \
 apk add --no-cache \
  chromium \
  chromium-chromedriver \
  docker && \
 echo "**** Install python deps ****" && \
 pip install --no-cache-dir \
  requests \
  selenium \
  docker \
  boto3 \
  anybadge \
  jinja2 && \
 echo "**** cleanup ****" && \
 rm -rf \
	/tmp/*

# copy local files
COPY ci /ci
