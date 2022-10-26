FROM ghcr.io/linuxserver/baseimage-ubuntu:jammy

# set version label
ARG BUILD_DATE
ARG VERSION
LABEL build_version="Linuxserver.io version:- ${VERSION} Build-date:- ${BUILD_DATE}"
LABEL maintainer="TheLamer"

RUN \
  echo "**** add 3rd party repos ****" && \
  mkdir -p /etc/apt/keyrings && \
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg | \
    gpg --dearmor -o /etc/apt/keyrings/docker.gpg && \
  curl -fsSL https://dl-ssl.google.com/linux/linux_signing_key.pub | \
    gpg --dearmor -o /etc/apt/keyrings/google.gpg && \
  echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu jammy stable" | \
    tee /etc/apt/sources.list.d/docker.list > /dev/null && \
  echo 'deb [arch=amd64 signed-by=/etc/apt/keyrings/google.gpg] http://dl.google.com/linux/chrome/deb/ stable main' > \
    /etc/apt/sources.list.d/google-chrome.list && \
  echo "**** install runtime packages ****" && \
  apt-get update && \
  apt-get install -y --no-install-recommends \
    docker-ce \
    google-chrome-stable \
    python3 \
    python3-pip \
    python3-setuptools \
    unzip \
    xserver-xephyr \
    xvfb && \
  echo "**** install chrome driver ****" && \
  CHROME_RELEASE=$(curl -sLk https://chromedriver.storage.googleapis.com/LATEST_RELEASE) && \
  echo "Retrieving Chrome driver version ${CHROME_RELEASE}" && \
  curl -sk -o \
    /tmp/chrome.zip -L \
    "https://chromedriver.storage.googleapis.com/${CHROME_RELEASE}/chromedriver_linux64.zip" && \
  cd /tmp && \
  unzip chrome.zip && \
  mv chromedriver /usr/bin/chromedriver && \
  chown root:root /usr/bin/chromedriver && \
  chmod +x /usr/bin/chromedriver && \
  echo "**** Install python deps ****" && \
  pip3 install --no-cache-dir \
    requests \
    selenium \
    docker \
    boto3 \
    anybadge \
    pyvirtualdisplay \
    jinja2 && \
  echo "**** cleanup ****" && \
  apt-get autoclean && \
  rm -rf \
    /var/lib/apt/lists/* \
    /var/tmp/* \
    /tmp/*

# copy local files
COPY ci /ci
COPY test_build.py test_build.py

ENTRYPOINT [ "" ]
