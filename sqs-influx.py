from datetime import datetime, timedelta
import logging
import os
from influxdb import InfluxDBClient
import boto3


try:
    import graypy
except ImportError:
    pass

# if we have log configuration for log servers, add that, otherwise let's use basic logging
isLogConfigInfo = False

gelf_url = os.getenv('GELF_SERVER_IP', '192.168.1.25')
influx_url = os.getenv('INFLUX_IP', '192.168.1.122')

log = logging.getLogger(__name__)

if gelf_url is not None:
    handler = graypy.GELFHandler(gelf_url, 12201, localname='water-sqs-influx')
    log.addHandler(handler)
    isLogConfigInfo = True

if not isLogConfigInfo:
    logging.basicConfig(level=logging.DEBUG)

log.setLevel(logging.DEBUG)
log.debug("Starting sqs to influx")
lastWriteMap = {}
influx_client = InfluxDBClient(influx_url, 8086, 'water_user', 'aquaman', 'water_readings')

def parse(line):
    log.debug('Received line: ' + line)
    current_value = float(line)
    now = datetime.utcnow()
    # should look up most recent reading
    query = 'SELECT * FROM water_usage GROUP BY * order by desc LIMIT 1'

    result = influx_client.query(query)

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
        usage = float(current_value-previous_value)
        log.debug('Value diff is: {0}'.format(usage))
    else:
        usage = float(0.0)
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
        influx_client.write_points(json_body)
        log.debug("write to influxdb")
    except ValueError:
        log.error('Invalid float for value: %s' % current_value)

def get_secret(secret_name):
    try:
        with open('/run/secrets/{0}'.format(secret_name), 'r') as secret_file:
            return secret_file.read()
    except IOError:
        return None

access_key_id = get_secret('aws_access_key_id').rstrip()
secret_access_key = get_secret('aws_secret_access_key').rstrip()
# Create SQS client
sqs = boto3.client('sqs',aws_access_key_id=access_key_id,aws_secret_access_key=secret_access_key,region_name='us-west-2')

queue_url = 'https://sqs.us-west-2.amazonaws.com/845159206739/sensors-maui-water'
log.debug("Listening to topic: {}".format(queue_url))
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
    if response != None:
        try:
            message = response['Messages'][0]
            elements = message['Body'].split(',')
            if elements[3] == '33228599':
                parse(elements[7])
            receipt_handle = message['ReceiptHandle']

            # Delete received message from queue
            sqs.delete_message(
                QueueUrl=queue_url,
                ReceiptHandle=receipt_handle
            )
        except KeyError:
            pass
    log.debug('Requesting another message')
