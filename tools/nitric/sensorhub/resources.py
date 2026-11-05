"""Resource names shared by all services.

Each service calls the helper it needs, so the .allow(...) list of that
service is the whole access it gets. Nitric turns it into a per-service role.
"""
from nitric.resources import api, bucket, topic

SPEED_TOPIC = "speed-readings"
LINE_VOLTAGE_TOPIC = "line-voltage-readings"


def sensorhub_api():
    return api("sensorhub")


# staging bucket
def staging_write():
    return bucket("staging").allow("write")


def staging_read():
    return bucket("staging").allow("read")


# results bucket
def results_write():
    return bucket("results").allow("write")


def results_read():
    return bucket("results").allow("read")


# topics (one per kind)
def speed_publish():
    return topic(SPEED_TOPIC).allow("publish")


def line_voltage_publish():
    return topic(LINE_VOLTAGE_TOPIC).allow("publish")


def speed_topic():
    return topic(SPEED_TOPIC)


def line_voltage_topic():
    return topic(LINE_VOLTAGE_TOPIC)
