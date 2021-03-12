FROM python:3.8-slim AS compile-image
RUN apt-get update
RUN apt-get install -y --no-install-recommends build-essential gcc git libxml2 libxslt-dev

RUN python -m venv /opt/venv
# Make sure we use the virtualenv:
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt


FROM python:3.8-slim AS build-image

COPY --from=compile-image /opt/venv /opt/venv

ENV PATH="/opt/venv/bin:$PATH"
WORKDIR /app
COPY sqs-influx.py .

EXPOSE 5000

CMD ["python","sqs-influx.py"]





