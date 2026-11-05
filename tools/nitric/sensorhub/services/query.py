from nitric.application import Nitric

from handlers import query
from resources import results_read, sensorhub_api

results = results_read()


@sensorhub_api().get("/readings")
async def get_readings(ctx):
    return await query(ctx, results)


Nitric.run()
