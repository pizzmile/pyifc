from nitric.application import Nitric

from handlers import process
from resources import results_write, speed_topic, staging_read

staging = staging_read()
results = results_write()


@speed_topic().subscribe()
async def parse_speed(ctx):
    await process(ctx, staging, results, "speed", "km/h", "stopped", "moving")


Nitric.run()
