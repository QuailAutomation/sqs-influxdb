from datetime import datetime
import logging
import os
import time
import _thread as thread
import influxdb_client
from influxdb_client import Point
from influxdb_client.client.write_api import SYNCHRONOUS

from flask import Flask, Response
from prometheus_client import Summary, Counter, Gauge, generate_latest
import boto3


log = logging.getLogger()
logging_level = os.getenv('LOG_LEVEL', logging.DEBUG)

# if gelf url is set and graypy import avail let's log there
try:
    import graypy
    gelf_url = os.getenv('GELF_SERVER_IP', None)
    if gelf_url is not None:
        handler = graypy.GELFHandler(gelf_url, 12201, localname='water-sqs-influx')
        log.addHandler(handler)
    else:
        logging.basicConfig(level=logging_level)
except ImportError:
    logging.basicConfig(level=logging_level)


log.debug("Starting sqs to influx")





delete_received_sqs = os.getenv('DELETE_RECEIVED_SQS', True)
log.debug(f"Deleting received sqs messages: {delete_received_sqs}")
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

influx_waterdb_token = get_secret('influx_waterdb_token') or os.getenv('influx_waterdb_token')
water_sqs_influxdb_org = get_secret('water_sqs_influxdb_org') or os.getenv('water_sqs_influxdb_org')
water_sqs_influxdb_bucket = get_secret('water_sqs_influxdb_bucket') or os.getenv('water_sqs_influxdb_bucket', default='water')
water_sqs_influxdb_url = get_secret('water_sqs_influxdb_url') or os.getenv('water_sqs_influxdb_url', default="influxdb")
log.debug(f"Influx ip: {water_sqs_influxdb_url}")


queue_url = 'https://sqs.us-west-2.amazonaws.com/845159206739/sensors-maui-water.fifo'

lastWriteMap = {}

influx_client = influxdb_client.InfluxDBClient(url=water_sqs_influxdb_url, token=influx_waterdb_token,org=water_sqs_influxdb_org)
buckets_api = influx_client.buckets_api()
query_api = influx_client.query_api()
write_api = influx_client.write_api()
# lets see if we have bucket 'water'
water_bucket = next((bucket for bucket in buckets_api.find_buckets().buckets if bucket.name == water_sqs_influxdb_bucket), None)
if water_bucket == None:
    # create the bucket
    log.info("'water' bucket not found, creating...")
    water_bucket = buckets_api.create_bucket(bucket_name=water_sqs_influxdb_bucket,org=water_sqs_influxdb_org)

def parse(value, reading_ts):
    log.debug(f'Received reading: {value}')
    current_value = int(value)
    WATER_METER_READING.set(current_value)
    now = datetime.utcnow()
    # should look up most recent reading
    # query = 'SELECT * FROM water_usage GROUP BY * order by desc LIMIT 1'

    startTime = time.time()
    current_reading_dt = datetime.fromtimestamp(reading_ts)
    # result = influx_client.query(query)
    
    query = f'from(bucket: "water")\
        |> range(start: 0, stop: {reading_ts})\
        |> filter(fn: (r) => r["_measurement"] == "water_usage")\
        |> filter(fn: (r) => r["_field"] == "value")\
        |> last()'
    
    result = query_api.query(org=water_sqs_influxdb_org, query=query)

    INFLUXDB_SUBMIT_DURATION.labels(operation="select").observe(time.time() - startTime)
    time_format = "%Y-%m-%dT%H:%M:%S"
    if len(result) == 1:
        previous_value = result[0].records[0].get_value()
        log.debug (f'last value: {previous_value}')
        usage = int(current_value-previous_value)
        # we dont have guarantee of order, so only fill in usage if there is a prior reading
        if usage < 0:
            usage = 0
        log.debug('Value diff is: {0}'.format(usage))
    else:
        usage = int(0.0)
    try:
        p = Point("water_usage").tag("house", "64 w mahi pua").field("value", current_value).field("usage",usage).time(datetime.fromtimestamp(reading_ts))

        startTime = time.time()
        write_api.write(bucket=water_sqs_influxdb_bucket, record=p)
        # influx_client.write_points(json_body)
        INFLUXDB_SUBMIT_DURATION.labels(operation="write").observe(time.time() - startTime)
        log.debug("write to influxdb")
    except ValueError as e:
        log.exception('Invalid int for value: {}, error: {}'.format(current_value,e))

# Create SQS client
sqs = boto3.client('sqs',aws_access_key_id=access_key_id,aws_secret_access_key=secret_access_key,region_name='us-west-2')



log.debug("Listening to topic: {}".format(queue_url))

app = Flask(__name__)


@app.route('/metrics')
def metrics():
    return Response(generate_latest(), mimetype=CONTENT_TYPE_LATEST)


def flask_thread():
    app.run(host='0.0.0.0',port=5000)

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
        WaitTimeSeconds=20
    )
    log.debug('Response: {}'.format(response))
    if response is not None:
        try:
            message = response['Messages'][0]
            elements = message['Body'].split(',')
            label_dict = {"meter_id":'33228599'}
            SENSOR_SAMPLES.labels(**label_dict).inc()
            sent_ts = int(response['Messages'][0]['Attributes']['SentTimestamp'])/1000 # was in millis
            dt_object = datetime.fromtimestamp(sent_ts)
            parse(elements[0],int(sent_ts))
            # log.debug("Received meter reading for: {}".format(elements[3]))
            receipt_handle = message['ReceiptHandle']

            if delete_received_sqs:
                # Delete received message from queue
                sqs.delete_message(
                    QueueUrl=queue_url,
                    ReceiptHandle=receipt_handle
                )
        except KeyError:
            pass
        except Exception as e:
            log.exception(e)
    time.sleep(40)
    log.debug('Requesting another message')
