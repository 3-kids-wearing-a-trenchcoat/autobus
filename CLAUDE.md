# Project: Autobus
Autobus is an autonomous bus routing program which determines which bus stops to visit and in what order, based on real-time passenger requests.  
The final program is meant to run on top of the Carla autonomous driving simulation, but for now the implementation will be limited to a proof-of-concept where the world on which the bus moves is reduced to a directed graph.

## Internal Logic
To be determined

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
* If something is ambiguous, inspect the existing code and follow its current patterns.
* If there is more than one sensible way to accomplish a task, offer both as options, list pros and cons for each approach, recommend one of these approaches and ask me which one to go with.
* Type hints required on all functions
* Stick to PEP 8 with 80 character line
* Do not install Python packages yourself, I will handle that.
* Code should be readable. To that end:
  * Give variables meaningful names
  * Split long functions into smaller functions whenever practical
  * Add annotations at any point in which the code is not simple to understand
* Code should be split into sensible packages according to their domain and in a way that keeps dependency "flowing in one direction.  
  Ask me before creating a new package, moving a piece of logic from one package to another and justify your request.

## How to run
Use `run.sh` to execute code. This is to make sure the correct Conda environment is used.

## Git Usage Rules
When working on this repository, you MUST follow these rules:

### Branching
* Always work on a dedicated branch
* If the branch does not exist, create it from `main`.
* If the branch already exists, switch to it.
* If a branch exists and you are not sure whether you should use it or not, ask before continuing.
* NEVER work directly on `main`.

### Allowed Git Operations
You are allowed to:
* `git checkout -b <branch>` (if needed)
* `git checkout <branch>`
* `git add`
* `git commit`
* `git revert` (only for commits on your branch)

### Forbidden Git Operations

You are STRICTLY FORBIDDEN from:
* `git push`
* `git merge`
* `git rebase`
* `git cherry-pick`
* `git pull` (unless explicitly instructed)
* Any operation that modifies branches other than your working branch
* Any operation that affects the `main` branch

### Safety Rules
* Never delete branches.
* Never rewrite history (`rebase`, `commit --amend`, etc.).
* Only modify files relevant to the current task.
* Keep commits small and descriptive.

### Summary
You may ONLY make local commits on your own branch.
You may NEVER integrate your changes into `main` or interact with remotes.
