from nitric.application import Nitric

from handlers import ingest
from resources import (
    line_voltage_publish,
    sensorhub_api,
    speed_publish,
    staging_write,
)

staging = staging_write()
publishers = {"speed": speed_publish(), "line_voltage": line_voltage_publish()}


@sensorhub_api().post("/readings")
async def post_reading(ctx):
    return await ingest(ctx, staging, publishers)


Nitric.run()
