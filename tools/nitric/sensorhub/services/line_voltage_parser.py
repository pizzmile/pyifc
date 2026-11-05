from nitric.application import Nitric

from handlers import process
from resources import line_voltage_topic, results_write, staging_read

staging = staging_read()
results = results_write()


@line_voltage_topic().subscribe()
async def parse_line_voltage(ctx):
    await process(
        ctx, staging, results, "line_voltage", "V", "no_voltage_observed", "voltage_observed"
    )


Nitric.run()
