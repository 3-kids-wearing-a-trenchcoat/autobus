# Running the Project

## Prerequisites

The project runs inside a Conda environment named `autobus`. Install
[Miniforge](https://github.com/conda-forge/miniforge) or Anaconda, then create
the environment from the project root:

```bash
conda env create -f environment.yml
```

## Running the simulation

Use `run.sh` from the project root to execute Python scripts inside the correct
Conda environment:

```bash
bash run.sh main.py
```

The script is a thin wrapper around `python`, so any arguments that would
normally follow `python` on the command line can be passed after the script
name:

```bash
bash run.sh main.py --some-flag value
```

## Running experiments (Jupyter)

Use `jupyter-run.sh` to launch JupyterLab:

```bash
bash jupyter-run.sh
```

The script sets `PYTHONPATH` to the project root before starting Jupyter, so
`src.*` imports work correctly from any notebook regardless of where it lives in
the repository. Experiment notebooks are kept in the `notebooks/` directory.

## Running the CARLA server

> **TODO:** This section will describe how to start the CARLA server and connect
> the simulation to it once the CARLA integration is implemented.
