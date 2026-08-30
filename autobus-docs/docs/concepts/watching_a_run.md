# Watching a run

A campaign answers *did it work*. This answers *what is it doing*, which is
a different question and needs a different tool: a single parametrised trial
with everything on screen and nothing written to disk, which is what the demo
(`./autobus.sh`) runs.

Reach for it when a number is strange and you want to see the bus earn it.

## The shape of it

```
run_trial(provider, policy, backend_factory=…)   ← ordinary trial, not recorded
        │
        └── CarlaBackend(debug_draw=…, viewer=…)
                 ├── world overlay      → drawn into CARLA, by DebugHelper
                 └── CarlaViewer        → its own pygame window, with a 2D HUD
```

The backend is the only thing that knows what the bus is doing, so it is
where both displays are fed from. Neither can change a result: they read
state that already exists, and a display that fails is dropped rather than
allowed to take the trial with it.

## Two surfaces, and why there are two

| | drawn by | shows |
|---|---|---|
| **World overlay** (`debug_draw=True`) | CARLA's `DebugHelper` | the route line, a box round every stop with its name, a permanent marker wherever a route was discarded |
| **Viewer window** (`viewer=True`) | pygame, over a camera sensor | counters, the plan, the live-request table, the minimap |

The split is forced, not chosen. **CARLA has no screen-space drawing.**
Everything `DebugHelper` offers is positioned in world metres, so a corner
of the screen is not a place you can draw. Anything that wants to be *on
the screen* rather than *in the town* needs a client-side window, which is
what `CarlaViewer` is — the same approach CARLA's own `manual_control.py`
takes.

So the world gets what is inherently about a place (where a stop is, which
road the bus was told to drive, where it lost the route), and the window
gets what is about the run (clocks, plans, tables).

### Working with `DebugHelper`

Read [Navigating in CARLA](carla_navigation.md) for the steering itself.
For drawing, three things are worth knowing before you spend an afternoon
on them, all measured against 0.9.16:

* **Use `draw_line`, `draw_point`, `draw_box` and `draw_string` only.** The
  `draw_hud_*` family is not a screen-space API — its arguments are
  `Location`s in metres exactly like the others, "HUD" naming the render
  layer — and on this version it renders nothing at all.
* **`draw_string` takes no font size.** It is a few pixels tall on a large
  display and no parameter changes that. `carla_debug_text` therefore draws
  text as line strokes, which have a real height and can be turned to face
  the camera. Stop labels are sized by distance so their apparent size holds
  roughly constant.
* **Shape lifetimes decay in simulated time**, so shapes from an earlier run
  survive into the next until the world has ticked past them. This looks
  exactly like a change not taking effect.

## The viewer window

`CarlaViewer` attaches an RGB camera behind the bus, renders it into a
pygame window, and draws over it in 2D:

* **counters**, top-left — simulated time, time on the current leg, the stop
  being driven to, waiting and aboard totals, and the plan;
* **the live-request table**, top-right — one row per request still in play:
  pickup, dropoff, time spent waiting, time spent riding, and which of the
  two it is doing. Longest wait first, since that passenger is the closest
  to giving up. Fulfilled and cancelled requests drop off it;
* **the minimap**, bottom-right, with two tabs (below).

It is a viewer and nothing else. It never advances the world, never touches
the bus, and closing the window only stops the drawing — the trial carries
on and the backend stops paying for a window nobody has.

### Two things it cannot do

**One window per process.** SDL gives a process a single window: a second
`set_mode` resizes the first rather than opening another. A second viewer is
refused outright, because two of them would draw into one surface and fight.
Sequential viewers, one per trial, are fine.

**It costs about 30% of tick rate** — that is the camera sensor rendering
and shipping frames, not the drawing. Never leave it on for anything you are
timing. `render_size` decouples the camera's resolution from the window's,
so a large window need not cost a large render.

`render_size=None` — the default, and `main.py`'s `DEFAULT_RENDER_WIDTH` /
`DEFAULT_RENDER_HEIGHT` — makes the camera match the window as it opens, and
costs no scaling at all. `main.py --render-size` sets it apart from the
window: below, to buy back tick rate on a large display; above, for a sharper
picture than the window alone would give.

**The window is resizable; nothing behind it is.** `size` is a *drawing*
resolution, not a window size: the surface everything is drawn on is fixed
for the whole run, and SDL fits it to whatever the window is dragged to
(`pygame.SCALED`). The camera's resolution is fixed for the same reason a
sensor cannot change resolution without being destroyed and respawned —
which would drop frames and cost a world tick per drag.

**So the run costs the same whatever the window is.** Measured on a 4K
window with a 1280x720 surface: 3.4 ms a frame, against 7.6 ms when the
surface followed the window. The old cost was `smoothscale` upscaling
960x540 to 4K on the CPU — 8 ms a redraw — and it grew with window area.
The price is that a maximised window shows an upscaled UI: text is softer
because it was drawn at the surface's resolution, not the window's. Ask for
a bigger `--viewer-size` if you want a crisper one, and pay for it.

SDL may report `no fast renderer available` on a machine with no accelerated
renderer; it falls back to scaling in software, which is still cheaper than
drawing at the window's size.

**The window manager has the last word on the opening size.** A compositor
that remembers window geometry can hand back a size nobody asked for; KDE's
"Remember Window Positions" script does exactly that, keyed on the window's
X11 class, which SDL takes from the script name. Measured: with a geometry
stored for that class, `--viewer-size` is overridden at creation and every
later `set_mode` in the same process is honoured. The viewer copes either
way — it lays its panels out from the surface it actually got, never from
the size it asked for.

Because the camera renders server-side, the server must be able to render —
but it does not have to render *for you*. A server drawing no viewport of its
own still produces frames for the camera, so watching a run needs a rendering
server rather than a *watchable* one.

## The minimap's two tabs

Both show the same stops. They disagree about what "far" means, and that
disagreement is the point.

**`town`** places stops where they are, over the road graph. It answers
*where is that stop*, and shows the plan as a chain through the stops with
the bus on the road edge it is currently on.

**`by time`** places stops by how long it takes to drive between them, laid
out by a force-directed pass. Separation on screen is drive time. A stop one
street away that costs five minutes to reach sits far off here and adjacent
on the other tab, and *that gap is the detour factor* — the thing the
campaign keeps measuring and no picture of a town can show. Stops nothing
can reach are ringed.

The layout is deterministic and started from the geographic positions, so it
stays recognisable and two runs can be compared.

### What a stop is called on screen

Every display — both tabs, the request table, and the label over the box in
the world — names a stop by its **authored short ID**, `s04`, not by the
`TransitGraph` node it lives on (`n_s04_n0272`). Two of the long form per
table row leaves no width for anything else, and a name over a stop is read
at a glance from a moving bus.

The node ID is still the key everywhere underneath: the `town` tab matches
it against the planned route to colour the next stop, and `by time` prices
it through the graph to lay itself out. The display name travels alongside
it, in `MiniMap.labels` for the panels and in `CarlaBackend`'s `stop_labels`
for the table and the world labels. Both are filled from the name the map
layer already records — `CarlaMapProvider` stores the authored stop ID as
each stop's name — by `minimap_from` and `stop_labels_from` respectively.

!!! warning "The short name is looked up, never read out of the node ID"
    `n_s04_n0272` does contain `s04`, and taking it from there looks like it
    works. It is a guess about which end of the edge the stop sits on, and on
    TAU it is wrong: **`n0257_n_s04` is stop `s02`**. That guess also
    *collides* with the real `s04`, which used to trip the ambiguity guard
    and send **every** stop in the town back to its full node ID — which is
    what filled the viewer with `n0102_n0129` instead of `s07`.

    The guess survives only as a fallback for a caller that passes no table,
    and is still abandoned wholesale if two stops would collide under it.

Diagnostics keep the full node ID deliberately — when a leg fails to route
or the bus cannot spawn, the edge the planner was working with is the
useful thing to name.

## Wiring your own

The notebook does this, and it is the whole contract:

```python
provider  = CarlaMapProvider("Town03")
minimap   = minimap_from(provider)          # plain coordinates

def build(graph, start_stop_id):            # the trial hands over the graph
    return CarlaBackend(
        stop_positions=stop_positions_from(provider),
        position=start_stop_id, town="Town03",
        no_rendering=False,                 # required: no render, no camera
        debug_draw=True,
        viewer=True, viewer_size=(1280, 720), render_size=None,
        minimap=minimap,
        schematic=schematic_from(graph, minimap.stops),
    )

run_trial(policy_factory=…, provider=provider, backend_factory=build, …)
```

Note what is *not* passed: no `TransitGraph`, no `MapProvider`, no policy.
`src/simulation` imports no map layer, so both displays are handed plain
numbers — `minimap_from` and `schematic_from` do that flattening, and are
duck-typed on the provider and the graph rather than importing them.

`schematic_from` needs travel costs, which only the `TransitGraph` has, and
the trial hands that to the backend factory. That is why it is built there
rather than beside the provider.

## Where the plan comes from

The bus's plan crosses the same seam:

```
BusAgent.planned_route()   → SimulationRunner → SimulationBackend.set_planned_route()
```

`set_planned_route` is an optional no-op on the backend contract: steering
never needs it, so no backend has to care, and `MockBackend` ignores it. It
carries a list of opaque stop IDs, so nothing below the algorithm layer
learns what a policy or a route object is.

Watching that list reshuffle is watching the policy re-plan, which no table
of results shows.

## Reading it against what you saw

1. **§2 of the notebook says lost** — `unroutable_legs > 0`, or driven
   distance more than 3× planned. The bus had no route at all and everything
   downstream is noise; what you watched was CARLA's traffic manager.
2. **Clean, but drops are listed.** It was on route and lost it at specific
   junctions. Every drop marker carries its timestamp, and the same
   timestamp appears in the drop table and in the leg table's `drops`
   column — one event, three views.
3. **No drops, but legs run long.** The route was kept and simply cost more
   than the estimate. Check the `by time` tab: a leg that is expensive there
   is expensive in the graph, not in the driving.
4. **Everything looks right and coverage is still bad.** Then it is the
   scenario, not the bus.
