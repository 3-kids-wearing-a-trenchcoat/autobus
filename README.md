# Autobus
University project for a bus which adapts its route based on demand.  
Work in progress

## Setup

```bash
conda env create -f environment.yml   # the environment
bash install-carla-agents.sh          # CARLA's agents package, not on PyPI
```

The second step is needed because the `carla` wheel ships client bindings
only; `agents.navigation` lives inside the CARLA image. See
[Running the Project](autobus-docs/docs/running.md) for the details, and for
how to run simulations, experiments and the docs.
