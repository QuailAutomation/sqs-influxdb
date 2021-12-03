import boto3

sqs_queue = 'sensors-maui-water.fifo'

def send_sqs(payload):
    sqs = boto3.resource('sqs')
    queue = sqs.get_queue_by_name(QueueName=sqs_queue)
    if len(payload) > 1:
        queue.send_message(MessageBody=payload,  MessageGroupId='maui-water')


send_sqs('Hola!')