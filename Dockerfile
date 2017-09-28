FROM apicht/rpi-golang:latest
#FROM armv7/armhf-ubuntu 
MAINTAINER craig

ARG git_commit
ARG version

RUN apt-get update && apt-get install -y \
    python2.7 \
    python-pip \
    gcc \
    --no-install-recommends && \
    rm -rf /var/lib/apt/lists/*


# Install Python requirements
ADD requirements.txt /tmp/requirements.txt
RUN pip install -r /tmp/requirements.txt
# Create runtime user
RUN useradd pi
RUN mkdir -p /home/pi
ADD sqs-influx.py /home/pi/sqs-influx.py
RUN mkdir -p /home/pi/.aws
ADD config /home/pi/.aws/config

USER pi

LABEL git-commit=$git_commit
LABEL version=$version

CMD ["python","/home/pi/sqs-influx.py"]
