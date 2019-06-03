FROM lsiobase/ubuntu:bionic
LABEL maintainer="TheLamer"

RUN \
 echo "**** install runtime packages ****" && \
 apt-get update && \
 apt-get install -y --no-install-recommends \
	gnupg \
	unzip && \
 curl -s \
        https://download.docker.com/linux/debian/gpg | \
        apt-key add - && \
 curl -s \
        https://dl-ssl.google.com/linux/linux_signing_key.pub | \
        apt-key add - && \
 echo 'deb [arch=amd64] https://download.docker.com/linux/ubuntu bionic stable' > \
        /etc/apt/sources.list.d/docker-ce.list && \
 echo 'deb [arch=amd64] http://dl.google.com/linux/chrome/deb/ stable main' > \
        /etc/apt/sources.list.d/google.list && \
 apt-get update && \
 apt-get install -y --no-install-recommends \
        docker-ce \
	google-chrome-stable \
	python-pip \
	python-setuptools && \
 echo "**** install chrome driver ****" && \
 CHROME_RELEASE=$(curl -sL https://chromedriver.storage.googleapis.com/LATEST_RELEASE) && \
 curl -o \
 /tmp/chrome.zip -L \
	"https://chromedriver.storage.googleapis.com/${CHROME_RELEASE}/chromedriver_linux64.zip" && \
 cd /tmp && \
 unzip chrome.zip && \
 mv chromedriver /usr/bin/chromedriver && \
 chown root:root /usr/bin/chromedriver && \
 chmod +x /usr/bin/chromedriver && \
 echo "**** Install python deps ****" && \
 pip install --no-cache-dir \
	requests \
	selenium \
	docker \
	boto3 \
	anybadge \
	jinja2 && \
 echo "**** cleanup ****" && \
 apt-get autoclean && \
 rm -rf \
	/var/lib/apt/lists/* \
	/var/tmp/* \
	/tmp/*

# copy local files
COPY ci /ci

ENTRYPOINT [ "" ]
