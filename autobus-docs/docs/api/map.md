# Map — API Reference

::: src.map.map_data

::: src.map.graph

::: src.map.MapProvider.MapProvider

::: src.map.PlanarMapProvider.PlanarMapProvider

::: src.map.StaticMapProvider.StopInput

::: src.map.StaticMapProvider.StaticMapProvider

::: src.map.CarlaMapProvider.CarlaMapProvider

::: src.map.carla_stops.CarlaStop

::: src.map.carla_stops.load_stops

::: src.map.carla_stops.save_stops

::: src.map.carla_stops.stops_path

::: src.map.carla_stops.town_name

::: src.map.carla_stops.available_towns

::: src.map.carla_topology.SegmentPoint

::: src.map.carla_topology.RoadSegment

::: src.map.carla_topology.CarlaTopology

::: src.map.carla_topology.load_topology

::: src.map.carla_topology.save_topology

::: src.map.carla_topology.topology_path

::: src.map.TransitGraph.TransitGraph

## Dumping and calibrating a CARLA town

Both need a live server, and neither is used at trial time — a town's roads
and its `traffic_efficiency` are dumped once and read from JSON thereafter.

::: src.map.carla_dump.dump_world

::: src.map.carla_dump.read_speed_limits

::: src.map.carla_dump.lane_change_along

::: src.map.carla_calibrate.calibrate

::: src.map.carla_calibrate.calibrate_policy_driven

::: src.map.carla_calibrate.drive_tour

::: src.map.carla_calibrate.check_speed_limits

::: src.map.carla_calibrate.report
