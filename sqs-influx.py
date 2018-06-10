from datetime import datetime, timedelta
import logging
import os
import time
import thread
from influxdb import InfluxDBClient
from flask import Flask, Response
from prometheus_client import Summary, Counter, Gauge, generate_latest
import boto3

log = logging.getLogger(__name__)

# if gelf url is set and graypy import avail let's log there
try:
    import graypy
    gelf_url = os.getenv('GELF_SERVER_IP', None)
    if gelf_url is not None:
        handler = graypy.GELFHandler(gelf_url, 12201, localname='water-sqs-influx')
        log.addHandler(handler)
except ImportError:
    logging.basicConfig(level=logging.DEBUG)

log.setLevel(logging.DEBUG)
log.debug("Starting sqs to influx")


influx_url = os.getenv('INFLUX_IP', '192.168.1.122')

# instrumentation
CONTENT_TYPE_LATEST = str('text/plain; version=0.0.4; charset=utf-8')
SENSOR_SAMPLES = Counter('maui_water_samples_submitted', 'Number of samples processed',['meter_id'])
WATER_METER_READING = Gauge('maui_water_meter_reading', 'Reading of the water meter')
INFLUXDB_SUBMIT_DURATION = Summary('maui_water_influxdb_operation_duration',
                           'Latency of submitting to InfluxDB',['operation'])
INFLUXDB_EXCEPTIONS = Counter('maui_water_mqtt_submit_exceptions_total',
                             'Exceptions thrown submitting to mqtt',['operation'])


# load swarm secrets
def get_secret(secret_name):
    try:
        with open('/run/secrets/{0}'.format(secret_name), 'r') as secret_file:
            return secret_file.read().rstrip()
    except IOError:
        return None

access_key_id = get_secret('aws_access_key_id') or os.getenv('aws_access_key_id')
secret_access_key = get_secret('aws_secret_access_key') or os.getenv('aws_secret_access_key')
influx_waterdb_user = get_secret('influx_waterdb_user') or os.getenv('influx_waterdb_user')
influx_waterdb_password = get_secret('influx_waterdb_password') or os.getenv('influx_waterdb_password')

lastWriteMap = {}
influx_client = InfluxDBClient(influx_url, 8086, influx_waterdb_user,influx_waterdb_password, 'water_readings')


def parse(line):
    log.debug('Received line: ' + line)
    current_value = int(line)
    WATER_METER_READING.set(current_value)
    now = datetime.utcnow()
    # should look up most recent reading
    query = 'SELECT * FROM water_usage GROUP BY * order by desc LIMIT 1'

    startTime = time.time()
    result = influx_client.query(query)
    INFLUXDB_SUBMIT_DURATION.labels(operation="select").observe(time.time() - startTime)
    time_format = "%Y-%m-%dT%H:%M:%S"
    if len(result) > 0:
        log.debug("last reading is: {0}".format(result))
        items = result.items()
        log.debug ('items: {0}'.format(items))
        points = result.get_points()
        log.debug ('points: {0}'.format(points))
        first_point = points.next()
        log.debug('first point: {0}'.format(first_point))
        previous_value = first_point["value"]
        log.debug ('recent value: ' + str(previous_value))
        recent_time = datetime.strptime(first_point["time"][:19],time_format)
        log.debug ('recent time: {0}'.format(recent_time))
        log.debug('Elapsed time: {0}'.format(int((now - recent_time).total_seconds() // 60)))  # minutes
        usage = int(current_value-previous_value)
        log.debug('Value diff is: {0}'.format(usage))
    else:
        usage = int(0.0)
    try:
        json_body = [
            {
                "measurement": "water_usage",
                "tags": {
                    "house": "64 w mahi pua",
                    "meterid": 33228599
                },
                "fields": {
                    "value": current_value,
                    "time": now.strftime(time_format),
                    "usage": usage
                }
            }
        ]
        log.debug('json to write: %s ' % json_body)
        startTime = time.time()
        influx_client.write_points(json_body)
        INFLUXDB_SUBMIT_DURATION.labels(operation="write").observe(time.time() - startTime)
        log.debug("write to influxdb")
    except ValueError as e:
        log.exception('Invalid int for value: {}, error: {}'.format(current_value,e))

# Create SQS client
sqs = boto3.client('sqs',aws_access_key_id=access_key_id,aws_secret_access_key=secret_access_key,region_name='us-west-2')

queue_url = 'https://sqs.us-west-2.amazonaws.com/845159206739/sensors-maui-water'
log.debug("Listening to topic: {}".format(queue_url))

app = Flask(__name__)


@app.route('/metrics')
def metrics():
    return Response(generate_latest(), mimetype=CONTENT_TYPE_LATEST)


def flask_thread():
    app.run(host='0.0.0.0')

thread.start_new_thread(flask_thread, ())

# is is loop to poll SQS
while True:
    # Long poll for message on provided SQS queue
    response = sqs.receive_message(
        QueueUrl=queue_url,
        AttributeNames=[
            'SentTimestamp'
        ],
        MaxNumberOfMessages=1,
        MessageAttributeNames=[
            'All'
        ],
        WaitTimeSeconds=60
    )
    log.debug('Response: {}'.format(response))
    if response is not None:
        try:

            message = response['Messages'][0]
            elements = message['Body'].split(',')
            label_dict = {"meter_id": elements[3]}
            SENSOR_SAMPLES.labels(**label_dict).inc()
            if elements[3] == '33228599':
                parse(elements[7])
            log.debug("Received meter reading for: {}".format(elements[7]))
            receipt_handle = message['ReceiptHandle']


            # Delete received message from queue
            sqs.delete_message(
                QueueUrl=queue_url,
                ReceiptHandle=receipt_handle
            )
        except KeyError:
            pass
        except Exception as e:
            log.exception(e)
    log.debug('Requesting another message')
