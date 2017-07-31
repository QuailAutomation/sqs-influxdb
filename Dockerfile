FROM apicht/rpi-golang:latest
#FROM armv7/armhf-ubuntu 
MAINTAINER craig

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
RUN chown -R pi /home/pi/
USER pi


CMD ["python","/home/pi/sqs-influx.py"]
