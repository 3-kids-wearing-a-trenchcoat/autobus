# Project: Autobus
Autobus is an autonomous bus routing program which determines which bus stops to visit and in what order, based on real-time passenger requests.  
The final program is meant to run on top of the Carla autonomous driving simulation, but for now the implementation will be limited to a proof-of-concept where the world on which the bus moves is reduced to a directed graph.

## Internal Logic
Anything not mentioned in this section has yet to be decided on, this covers only the approaches we've settled on so far.
The docs _should_ be accurate and may be consulted when in doubt. But be wary that we may slip up and forget to update them.

  ### Map layer
  * `MapProvider` is an abstraction over the map source (CARLA, planar graph, hand-authored static data).
  * Internally, the map is represented as a `TransitGraph`: a directed graph whose **nodes are road edges** from the underlying map and
  whose graph edges represent legal transitions between them. Bus stops are attributes on certain nodes.
  
  ### Requests
  * `Request` is a dataclass carrying a pickup stop, a dropoff stop, and a lifecycle state (`PENDING → ON_BUS → FULFILLED`, or
  `CANCELLED`).
  * `RequestPool` is the single shared store for all requests. The bus agent writes state transitions; the demand generator adds new
  requests; a future metrics component can read history.
  
  ### Demand generation
  * `DemandGenerator` adds new `Request` objects to the pool on each simulation tick by delegating to a `GenerationPolicy` (e.g.
  `PeriodicPolicy`) to decide how many to emit.
  
  ### Routing
  * `BusState` is a snapshot passed to the policy on each planning call: current position, onboard passengers, pending requests, and
  remaining capacity.
  * `DispatchPolicy` is an abstract base class with one method — `plan(state, n_stops) → list[stop_id]` — that returns an ordered list of
  stops to visit. Concrete implementations contain the actual routing logic.
  * `BusAgent` holds the bus's mutable state (position, onboard passengers, planned route). On each stop arrival it: (1) alights and
  boards passengers, updating the pool; (2) re-plans by projecting state forward past a configurable **commitment window** and calling
  the policy. Stops inside the commitment window are locked and not replaced by the re-plan.


## Stack
The program should be written in Python 3.12.  
New libraries should only be introduced under one the following circumstances:
* A feature can't be implemented using the existing libraries 
* Implementing a feature with existing libraries would be less efficient.
* Implementing a feature with a new library would be "cleaner" in terms of readability.

If you want to add a new library or module, you must first explain to me why you want to do it, whether or not a feature _could_ be implemented without introducing this new library and if so what might implementing it with existing libraries look like.  
You may only add this library or module after my explicit permission.  
If you get this permission, make sure to also update `environment.yml` to reflect the change.

## Working rules
* Each modification to the code (be it changing, adding or deleting) must be briefly explained and applied only after my explicit permission.
* When editing, make the smallest change that solves the request.
* Do not rename files or reorganize directories unless asked.
* Commit changes you've made according to the Git Usage Rules outlined below.
* If you create new files under `/src`, `/tests` or `/docs`, add them to git.
* If something is ambiguous, inspect the existing code and follow its current patterns.
* If there is more than one sensible way to accomplish a task, offer both as options, list pros and cons for each approach, recommend one of these approaches and ask me which one to go with.
* Type hints required on all functions
* Stick to PEP 8 with 80 character line
* Do not install Python packages yourself, I will handle that.
* Code should be readable. To that end:
  * Give variables meaningful names
  * Split long functions into smaller functions whenever practical
  * Add annotations at any point in which the code is not simple to understand
* Code should be split into sensible packages according to their domain and in a way that keeps dependency "flowing" in one direction.  
  Ask me before creating a new package, moving a piece of logic from one package to another and justify your request.
* Include docstrings. For simple functions and classes, one-line docstrings are enough. 
  For non-trivial functions, docstrings should be of the following format:  
  ```
  """
  Short description of the function
  :param x: specify what input parameter x is and what's its type
  (there should be one param line for each input parameter)
  :return: what this function returns
  """
  ```
* Leave TODO comments alone unless they're directly relevant to the task at hand.
* Docs should be kept updated to make sure they faithfully reflect the project at its current state.
  * API references are automatically generated in docs from docstrings
  * When new "concepts" are added (new classes, new logic, etc), those need to be described and added to the docs manually.  
    This can be postponed until after some implementation has been made, verified and approved, but it's important not to forget about it.

## How to run
Always use `run.sh` to execute code. This is to make sure the correct Conda environment is used.  
This script simply runs `conda run -n "$ENV_NAME" python "$@"`, so `run.sh` needs to be supplied only with the 
commands that would appear after `python` if you were to write it manually. (`$ENV_NAME` is a constant defined in the script)

## Git Usage Rules
When working on this repository, you MUST follow these rules:

### Branching
* Work on the single existing branch that isn't `main`. Consolidate work
  onto that one working branch rather than juggling several in parallel.
* If no such branch exists (e.g. the previous one was merged and deleted),
  create a new one from `main`.
* If a branch exists and you are not sure whether you should use it or not, ask before continuing.
* Before applying git operation, make sure you're on the intended branch. As I may merge and delete branches and forget to notify you.
  If that happens, you may recreate the branch.
* NEVER work directly on `main`.

### Allowed Git Operations
You are allowed to:
* `git checkout -b <branch>` (if needed)
* `git checkout <branch>`
* `git add`
* `git commit`
* `git revert` (only for commits on your branch)
* `git merge <branch>` — LOCAL merges into your working branch only (e.g.
  consolidating another feature branch onto it). Never into `main`.

### Forbidden Git Operations

You are STRICTLY FORBIDDEN from:
* `git push`
* `git rebase`
* `git cherry-pick`
* `git pull` (unless explicitly instructed)
* Any operation that affects the `main` branch (including merging into it)

### Safety Rules
* Never delete branches.
* Never rewrite history (`rebase`, `commit --amend`, etc.).
* Only modify files relevant to the current task.
* Keep commits small and descriptive.

### Summary
You may make local commits, and local merges between feature branches, on
your own working branch.
You may NEVER integrate your changes into `main` or interact with remotes.
