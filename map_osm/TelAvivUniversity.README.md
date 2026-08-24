# TelAvivUniversity — a CARLA map built from OpenStreetMap

Derived from `Tel-Aviv-University.osm`. Nothing here is hand-edited; every
file is reproducible from that extract plus the steps below.

| file | what it is |
|---|---|
| `TelAvivUniversity.osm` | the extract cropped to the campus polygon plus a 150 m road margin |
| `TelAvivUniversity.xodr` | that crop converted to OpenDRIVE — the map CARLA loads |
| `TelAvivUniversity.stops.json` | 25 bus stops, one per campus point of interest |

The map the project actually loads lives in `src/map/carla_maps/` as
`TAU.{xodr,stops.json,topology.json}` — the `.xodr` and `.stops.json` there
are byte-identical copies of the two above. **There is deliberately no
`topology.json` here.** An earlier one was kept alongside these files and went
stale: it had been dumped from a different generation of the world (552
segments against the live 557, different road ids) and was never calibrated,
so it read `traffic_efficiency` 1.0 where the live map reads 0.6994. The dump
is regenerable at any time with `./run.sh -m src.preparation.carla_dump
--town TAU`, and the calibrated one in `carla_maps/` is the only authority.

## How it was made

1. **Crop** to the `amenity=university` polygon with a **150 m road margin**.
   The margin is load-bearing: cropping to the polygon exactly leaves only
   222 driving lanes and a 3% strongly-connected core, against 533 lanes and
   a 44% core with it. The perimeter roads carry the loops that make round
   trips possible.
2. **Convert** with `carla.Osm2Odr`, way types including `service` and
   `living_street`, and `Osm2OdrSettings.proj_string` set explicitly to
   `+proj=tmerc +lat_0=32.11217 +lon_0=34.804085 +k=1 +x_0=0 +y_0=0
   +ellps=WGS84 +units=m +no_defs`. CARLA's default emits a bare
   `+proj=tmerc` it cannot itself parse, which makes lat/lon unrecoverable.
3. **Load** with `client.generate_opendrive_world` and
   `OpendriveGenerationParameters(wall_height=0.0, additional_width=4.0, ...)`.
   Both matter — CARLA's default `wall_height=1.0` wedges the bus against
   generated walls, and a narrow road drops it out of the world entirely.
4. **Dump** the world, taking per-road speed limits from the `.xodr`'s
   per-lane `<speed max>` elements. A generated map carries **no** type-274
   landmarks, so `read_speed_limits` finds nothing on its own.
5. **Cap those limits at 50 km/h.** Only 29 of the 479 roads have a real
   OpenStreetMap `maxspeed`; the rest carry the converter's per-highway-type
   defaults, which are motorway numbers — 9.2 km of campus at 100 km/h and
   7.6 km at 80, together 70% of its length.

## The stops

One per point of interest, taken from OSM and placed on the largest
strongly-connected component of the road graph:

* **9 named vehicle gates** — gate nodes that sit on a drivable way, which is
  how the inner side of an entrance is inferred. Unnamed gates are excluded;
  they are bollards and service barriers.
* **12 building clusters** — named buildings whose centroid falls inside the
  campus, merged when within 120 m so a quadrangle gets one stop, named after
  the largest member.
* **4 dormitory clusters.**

Measured on this map: **600/600 stop pairs routable**, median leg 108 s
(p90 168 s) once the speed cap is applied, nearest-neighbour spacing median
73 m, and 8/8 driven legs.

## Reading position

World position of a stop is `tmerc(lat, lon)` plus the `<offset>` in the
`.xodr` header, with **y negated** for CARLA's left-handed axes. CARLA's own
`transform_to_geolocation` ignores that offset and disagrees by about a
kilometre — trust the header, not the API.

## One hand edit

`Tel-Aviv-University.osm` is untouched; the crop here carries a single
repair. Gate 4 (Ramniceanu) was drawn as two opposing one-way carriageways
about 7 m apart. That is correct on the ground, and fatal after conversion:
`additional_width=4.0` widens each to roughly 11 m, so the two surfaces
overlap and the seam between them is a lip. The bus climbed it, ended up
rolled 16 degrees and sunk 19 cm, and never got out again -- **3 of 24 legs
arrived**, because every leg after the first failure began already trapped.

The outbound lane (way `99239233`) is deleted and the inbound one
(`46766042`) made two-way, which is the same road with one surface. A
double junction 30 m north (nodes `597659053` and `1147945583`, joined by
the 5.5 m stub way `99239231`) is collapsed into one node for the same
reason.

Re-derive with `collapse_node.py` then `single_carriageway.py`, or redo it
by hand -- it is four elements.

## Known limits

* **18 of 24 legs of the full tour arrive.** The six that do not are
  isolated spots where the generated surface still catches the bus; each
  costs one leg and none cascade. `stats()["unwedges"]` and
  `stats()["falls_recovered"]` count the rescues.
* Two stops sit further from what they serve than is realistic: the Anu
  Museum cluster (192 m) and gate G5 (189 m). No nearer reachable road exists.
* Seven stops trip `"matches 2 segments; using the first"` — harmless, but a
  sign that stops land on lanes carrying more than one segment far more often
  here than on the built-in towns.
* The dumped topology contains a small number of junction-internal edges that
  `waypoint.next()` can never produce, so `TransitGraph` can promise a leg
  `plan_route` refuses. Stops are kept off junction interiors to avoid it.
